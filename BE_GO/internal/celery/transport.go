package celery

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"
)

// kombu.transport.redis Channel defaults (no broker_transport_options are
// configured in BE/core/settings.py).
const (
	unackedKey          = "unacked"
	unackedIndexKey     = "unacked_index"
	unackedMutexKey     = "unacked_mutex"
	unackedMutexExpire  = 300 * time.Second
	DefaultVisibility   = 3600 * time.Second
	DefaultRestoreEvery = 100 * time.Second // maybe_restore_messages every 10 s, restore_visible(interval=10)
)

var prioritySteps = []int64{0, 3, 6, 9}

// Transport is kombu's Redis transport with ack emulation, as a Celery
// worker consuming one queue uses it: BRPOP over the priority sub-queues,
// the unacked hash and unacked_index sorted set (QoS.append, ack, reject),
// restore of messages past the visibility timeout under unacked_mutex,
// and restore of held messages at shutdown.
type Transport struct {
	Client     redis.UniversalClient
	Queue      string
	Visibility time.Duration
	Now        func() time.Time
}

// Delivery is one message taken from the queue.
type Delivery struct {
	Raw []byte
	// Message is nil when the payload is outside the decoded subset.
	Message *Message
	Err     error
}

func (t *Transport) now() time.Time {
	if t.Now != nil {
		return t.Now()
	}
	return time.Now()
}

func queueForPriority(queue string, priority int64) string {
	step := prioritySteps[0]
	for _, s := range prioritySteps {
		if priority >= s {
			step = s
		}
	}
	if step == 0 {
		return queue
	}
	return queue + bindingSeparator + strconv.FormatInt(step, 10)
}

func binding(queue string) (string, string) {
	return "_kombu.binding." + queue, queue + bindingSeparator + bindingSeparator + queue
}

// Declare is the worker's queue declaration (Channel._queue_bind).
func (t *Transport) Declare(ctx context.Context) error {
	key, member := binding(t.Queue)
	return t.Client.SAdd(ctx, key, member).Err()
}

// Get is _brpop_start/_brpop_read followed by QoS.append for a decodable
// message. It returns nil when the queue stayed empty for timeout.
func (t *Transport) Get(ctx context.Context, timeout time.Duration) (*Delivery, error) {
	keys := make([]string, len(prioritySteps))
	for i, step := range prioritySteps {
		keys[i] = queueForPriority(t.Queue, step)
	}
	item, err := t.Client.BRPop(ctx, timeout, keys...).Result()
	if errors.Is(err, redis.Nil) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	d := &Delivery{Raw: []byte(item[1])}
	d.Message, d.Err = ParseMessage(d.Raw)
	if d.Err != nil {
		return d, nil
	}
	stored, err := EncodeJSON([]any{d.Message.Envelope, d.Message.Exchange, d.Message.RoutingKey})
	if err != nil {
		d.Message, d.Err = nil, err
		return d, nil
	}
	tag := d.Message.DeliveryTag
	_, err = t.Client.TxPipelined(ctx, func(pipe redis.Pipeliner) error {
		pipe.ZAdd(ctx, unackedIndexKey, redis.Z{Score: float64(t.now().UnixNano()) / 1e9, Member: tag})
		pipe.HSet(ctx, unackedKey, tag, stored)
		return nil
	})
	if err != nil {
		return nil, fmt.Errorf("celery: unacked bookkeeping: %w", err)
	}
	return d, nil
}

// Ack is QoS.ack: the message leaves the unacked hash and index.
func (t *Transport) Ack(ctx context.Context, tag string) error {
	_, err := t.Client.TxPipelined(ctx, func(pipe redis.Pipeliner) error {
		pipe.ZRem(ctx, unackedIndexKey, tag)
		pipe.HDel(ctx, unackedKey, tag)
		return nil
	})
	return err
}

// Reject is QoS.reject: with requeue the message is restored (marked
// redelivered) at the far end of its queue (restore_by_tag leftmost), else it
// is dropped like an acknowledgement.
func (t *Transport) Reject(ctx context.Context, tag string, requeue bool) error {
	if requeue {
		return t.RestoreByTag(ctx, tag, true)
	}
	return t.Ack(ctx, tag)
}

// Publish declares queue and pushes payload (a message the running task
// publishes, as Celery's producer does).
func (t *Transport) Publish(ctx context.Context, queue string, payload []byte) error {
	return RedisBroker{Client: t.Client}.Push(ctx, queue, payload)
}

// Handoff moves a delivered message to queue in one transaction: the
// envelope with delivery_info.routing_key set to queue (the message Django
// would have published without the route) is pushed and the delivery is
// acknowledged. A payload outside the decoded subset is pushed unchanged.
func (t *Transport) Handoff(ctx context.Context, d *Delivery, queue string) error {
	payload := d.Raw
	tag := ""
	if d.Message != nil {
		tag = d.Message.DeliveryTag
		envelope := Clone(d.Message.Envelope).(Dict)
		properties, _ := envelope.Get("properties")
		info, _ := properties.(Dict).Get("delivery_info")
		info.(Dict).Set("routing_key", queue)
		var err error
		if payload, err = EncodeJSON(envelope); err != nil {
			return err
		}
	}
	key, member := binding(queue)
	_, err := t.Client.TxPipelined(ctx, func(pipe redis.Pipeliner) error {
		pipe.SAdd(ctx, key, member)
		pipe.LPush(ctx, queue, payload)
		if tag != "" {
			pipe.ZRem(ctx, unackedIndexKey, tag)
			pipe.HDel(ctx, unackedKey, tag)
		}
		return nil
	})
	return err
}

// lookup is Channel._lookup for the anonymous exchange (the routing key is
// the queue) and for a direct exchange (its bindings).
func (t *Transport) lookup(ctx context.Context, exchange, routingKey string) ([]string, error) {
	if exchange == "" {
		return []string{routingKey}, nil
	}
	members, err := t.Client.SMembers(ctx, "_kombu.binding."+exchange).Result()
	if err != nil {
		return nil, err
	}
	var queues []string
	for _, member := range members {
		parts := strings.Split(member, bindingSeparator)
		if len(parts) == 3 && parts[0] == routingKey {
			queues = append(queues, parts[2])
		}
	}
	return queues, nil
}

// RestoreByTag is QoS.restore_by_tag: under WATCH unacked, the stored
// message leaves the unacked hash and index and, marked redelivered in its
// headers and delivery_info, is pushed back on its queue (RPUSH, the next
// one BRPOP takes; LPUSH when leftmost).
func (t *Transport) RestoreByTag(ctx context.Context, tag string, leftmost bool) error {
	return t.Client.Watch(ctx, func(tx *redis.Tx) error {
		stored, err := tx.HGet(ctx, unackedKey, tag).Result()
		if err != nil && !errors.Is(err, redis.Nil) {
			return err
		}
		var queues []string
		var payload []byte
		priority := int64(0)
		if stored != "" {
			payload, queues, priority, err = t.restorable(ctx, stored)
			if err != nil {
				return err
			}
		}
		_, err = tx.TxPipelined(ctx, func(pipe redis.Pipeliner) error {
			pipe.ZRem(ctx, unackedIndexKey, tag)
			pipe.HDel(ctx, unackedKey, tag)
			for _, queue := range queues {
				if leftmost {
					pipe.LPush(ctx, queueForPriority(queue, priority), payload)
				} else {
					pipe.RPush(ctx, queueForPriority(queue, priority), payload)
				}
			}
			return nil
		})
		return err
	}, unackedKey)
}

// restorable is _do_restore_message's payload: headers.redelivered and
// delivery_info.redelivered set to True (missing keys are left alone).
func (t *Transport) restorable(ctx context.Context, stored string) ([]byte, []string, int64, error) {
	value, err := DecodeJSON([]byte(stored))
	if err != nil {
		return nil, nil, 0, err
	}
	triple, ok := value.([]any)
	if !ok || len(triple) != 3 {
		return nil, nil, 0, fmt.Errorf("%w: unacked entry", ErrUndecodable)
	}
	envelope, ok := triple[0].(Dict)
	exchange, ok1 := triple[1].(string)
	routingKey, ok2 := triple[2].(string)
	if !ok || !ok1 || !ok2 {
		return nil, nil, 0, fmt.Errorf("%w: unacked entry", ErrUndecodable)
	}
	priority := int64(0)
	if headers, present := envelope.Get("headers"); present {
		if properties, present := envelope.Get("properties"); present {
			if h, ok := headers.(Dict); ok {
				if p, ok := properties.(Dict); ok {
					if info, present := p.Get("delivery_info"); present {
						if i, ok := info.(Dict); ok {
							envelope.Set("headers", h.Set("redelivered", true))
							envelope.Set("properties", p.Set("delivery_info", i.Set("redelivered", true)))
						}
					}
					if n, ok := integer(func() any { v, _ := p.Get("priority"); return v }()); ok {
						priority = min(max(n, 0), 9)
					}
				}
			}
		}
	}
	payload, err := EncodeJSON(envelope)
	if err != nil {
		return nil, nil, 0, err
	}
	queues, err := t.lookup(ctx, exchange, routingKey)
	return payload, queues, priority, err
}

// RestoreVisible is QoS.restore_visible(num=None): under the unacked_mutex
// lock (skipped when another worker holds it), every message delivered
// longer than the visibility timeout ago is restored.
func (t *Transport) RestoreVisible(ctx context.Context) error {
	token := make([]byte, 16)
	if _, err := rand.Read(token); err != nil {
		return err
	}
	value := hex.EncodeToString(token)
	acquired, err := t.Client.SetNX(ctx, unackedMutexKey, value, unackedMutexExpire).Result()
	if err != nil || !acquired {
		return err
	}
	defer releaseMutex.Run(context.WithoutCancel(ctx), t.Client, []string{unackedMutexKey}, value)
	visibility := t.Visibility
	if visibility <= 0 {
		visibility = DefaultVisibility
	}
	ceil := float64(t.now().Add(-visibility).UnixNano()) / 1e9
	tags, err := t.Client.ZRevRangeByScore(ctx, unackedIndexKey, &redis.ZRangeBy{
		Max: strconv.FormatFloat(ceil, 'f', -1, 64), Min: "0"}).Result()
	if err != nil {
		return err
	}
	for _, tag := range tags {
		if err = t.RestoreByTag(ctx, tag, false); err != nil {
			return err
		}
	}
	return nil
}

// releaseMutex is redis-py Lock.release's compare-and-delete script.
var releaseMutex = redis.NewScript(`if redis.call("get", KEYS[1]) == ARGV[1] then return redis.call("del", KEYS[1]) else return 0 end`)
