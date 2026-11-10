package celery

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

// Remote control (celery.app.control, kombu.pidbox.Mailbox) over kombu's
// Redis transport, as app.control.inspect(timeout=...).ping() sends it from
// a fresh process (BE/instance/diagnostic_probe.py runs it in a subprocess):
//
//   - the reply queue '<oid>.reply.celery.pidbox' is bound to the direct
//     exchange 'reply.celery.pidbox' with routing key oid (SADD on
//     '_kombu.binding.reply.celery.pidbox');
//   - the command is published on the fanout exchange 'celery.pidbox'
//     (PUBLISH on '/0.celery.pidbox', the fanout prefix '/{db}.' of DB 0);
//   - replies are taken with BRPOP from the reply queue's priority lists
//     until no reply arrives for the timeout (Mailbox._collect drains with
//     drain_events(timeout=timeout) until socket.timeout);
//   - the reply queue is then deleted (after_reply_message_received:
//     SREM of the binding and DEL of the four priority lists).
//
// A worker answers each command with {hostname: reply} published to the reply
// exchange with the 'ticket' header of the request.
const (
	pidboxExchange      = "celery.pidbox"
	pidboxReplyExchange = "reply.celery.pidbox"
	// pidboxFanoutTopic is _get_publish_topic('celery.pidbox', '') for DB 0.
	pidboxFanoutTopic = "/0." + pidboxExchange
)

// ErrControlReply is a reply kombu would not decode (wrong content type or
// encoding, not JSON): the inspect call raises.
var ErrControlReply = errors.New("celery: undecodable control reply")

// ControlClient is the broker connection (Redis DB 0) the control commands use.
type ControlClient interface {
	SAdd(ctx context.Context, key string, members ...any) *redis.IntCmd
	SRem(ctx context.Context, key string, members ...any) *redis.IntCmd
	Del(ctx context.Context, keys ...string) *redis.IntCmd
	Publish(ctx context.Context, channel string, message any) *redis.IntCmd
	BRPop(ctx context.Context, timeout time.Duration, keys ...string) *redis.StringSliceCmd
}

// Broadcast is one Mailbox._broadcast(command, arguments={}, reply=True):
// the identifiers it generated and the replies it collected, in arrival order.
type Broadcast struct {
	Ticket, OID string
	// Replies are the decoded reply bodies whose ticket matches (kombu keeps
	// the others as unclaimed replies of other calls).
	Replies []any
}

// pidboxEnvelope is the kombu Redis message for body (Channel.prepare_message
// plus basic_publish's body_encoding/delivery_tag), with the headers and
// properties in kombu's order.
func pidboxEnvelope(body Dict, headers Dict, exchange, routingKey string, deliveryMode int, tag string) ([]byte, error) {
	encoded, err := EncodeJSON(body)
	if err != nil {
		return nil, err
	}
	return EncodeJSON(Dict{
		{"body", base64.StdEncoding.EncodeToString(encoded)},
		{"content-encoding", "utf-8"},
		{"content-type", "application/json"},
		{"headers", headers},
		{"properties", Dict{{"delivery_mode", deliveryMode},
			{"delivery_info", Dict{{"exchange", exchange}, {"routing_key", routingKey}}},
			{"priority", 0}, {"body_encoding", "base64"}, {"delivery_tag", tag}}},
	})
}

// ControlMessage is the broadcast envelope for command, as a fresh process
// publishes it (its Lamport clock forwards to 1; 'expires' is time() + timeout).
func ControlMessage(command, ticket, oid, tag string, now time.Time, timeout time.Duration) ([]byte, error) {
	body := Dict{{"method", command}, {"arguments", Dict{}}, {"destination", nil}, {"pattern", nil}, {"matcher", nil},
		{"ticket", ticket}, {"reply_to", Dict{{"exchange", pidboxReplyExchange}, {"routing_key", oid}}}}
	expires := float64(now.UnixNano())/1e9 + timeout.Seconds()
	headers := Dict{{"clock", 1}, {"expires", Number(pythonFloat(expires))}}
	return pidboxEnvelope(body, headers, pidboxExchange, "", 2, tag)
}

func pidboxReplyQueue(oid string) (key, member string, lists []string) {
	queue := oid + "." + pidboxReplyExchange
	for _, step := range prioritySteps {
		lists = append(lists, queueForPriority(queue, step))
	}
	return "_kombu.binding." + pidboxReplyExchange, oid + bindingSeparator + bindingSeparator + queue, lists
}

// Inspect is app.control.inspect(timeout=timeout).<command>() without
// destination: it broadcasts command and collects the replies until none
// arrives within timeout (or ctx ends). The reply queue is removed before it
// returns, also on failure.
func Inspect(ctx context.Context, client ControlClient, command string, timeout time.Duration, now func() time.Time) (*Broadcast, error) {
	if now == nil {
		now = time.Now
	}
	b := &Broadcast{Ticket: uuid.NewString(), OID: uuid.NewString()}
	key, member, lists := pidboxReplyQueue(b.OID)
	if err := client.SAdd(ctx, key, member).Err(); err != nil {
		return nil, err
	}
	defer func() {
		cleanup, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
		defer cancel()
		client.SRem(cleanup, key, member)
		for _, list := range lists {
			client.Del(cleanup, list)
		}
	}()
	message, err := ControlMessage(command, b.Ticket, b.OID, uuid.NewString(), now(), timeout)
	if err != nil {
		return nil, err
	}
	if err = client.Publish(ctx, pidboxFanoutTopic, message).Err(); err != nil {
		return nil, err
	}
	for {
		item, err := client.BRPop(ctx, timeout, lists...).Result()
		if errors.Is(err, redis.Nil) {
			return b, nil
		}
		if err != nil {
			return nil, err
		}
		body, claimed, err := decodeControlReply([]byte(item[1]), b.Ticket, now())
		if err != nil {
			return nil, err
		}
		if claimed {
			b.Replies = append(b.Replies, body)
		}
	}
}

// decodeControlReply is Mailbox._collect's on_message for one Redis message:
// Message(payload).decode() with accept=['json'] (body_encoding base64,
// content-encoding utf-8, json.loads), then the 'expires' header (an
// expired reply is dropped) and the 'ticket' header (absent: the call's own
// ticket; another ticket is a reply to another call). claimed reports a
// reply of this call.
func decodeControlReply(raw []byte, ticket string, now time.Time) (any, bool, error) {
	value, err := DecodeJSON(raw)
	if err != nil {
		return nil, false, err
	}
	envelope, ok := value.(Dict)
	if !ok {
		return nil, false, ErrControlReply
	}
	field := func(d Dict, name string) any { v, _ := d.Get(name); return v }
	properties, _ := field(envelope, "properties").(Dict)
	headers, _ := field(envelope, "headers").(Dict)
	contentType, _ := field(envelope, "content-type").(string)
	encoding, _ := field(envelope, "content-encoding").(string)
	text, isText := field(envelope, "body").(string)
	if contentType != "application/json" || (encoding != "utf-8" && encoding != "utf8") || !isText {
		return nil, false, ErrControlReply
	}
	payload := []byte(text)
	if bodyEncoding, _ := field(properties, "body_encoding").(string); bodyEncoding == "base64" {
		if payload, err = base64.StdEncoding.DecodeString(text); err != nil {
			return nil, false, fmt.Errorf("%w: %v", ErrControlReply, err)
		}
	}
	body, err := DecodeJSON(payload)
	if err != nil {
		return nil, false, err
	}
	if value, present := headers.Get("expires"); present && value != nil {
		switch v := value.(type) {
		case Number:
			seconds, ok := numberFloat(v)
			if !ok {
				return nil, false, ErrControlReply
			}
			if seconds != 0 && float64(now.UnixNano())/1e9 > seconds {
				return body, false, nil
			}
		case bool:
			if v {
				return body, false, nil // time() > True
			}
		case string:
			if v != "" {
				return nil, false, ErrControlReply // float > str raises
			}
		default:
			return nil, false, ErrControlReply
		}
	}
	if value, present := headers.Get("ticket"); present {
		own, ok := value.(string)
		return body, ok && own == ticket, nil
	}
	return body, true, nil
}

func numberFloat(value any) (float64, bool) {
	n, ok := value.(Number)
	if !ok {
		return 0, false
	}
	var f float64
	_, err := fmt.Sscan(string(n), &f)
	return f, err == nil
}
