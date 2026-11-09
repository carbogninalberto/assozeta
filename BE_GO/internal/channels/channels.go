// Package channels sends Django Channels group messages from Go.
//
// Layer.GroupSend reproduces what Django's configured channel layer,
// notifications.realtime_layer.GoMirroredRedisChannelLayer, does for
// async_to_sync(channel_layer.group_send)(group, message) with the pinned
// channels 4.2.0 / channels_redis 4.2.1 / msgpack 1.2.3 / redis-py 5.2.1 and
// the BE/core/settings.py CHANNEL_LAYERS config (one Redis host, DB 0, every
// other option default: prefix "asgi", expiry 60, group_expiry 86400,
// capacity 100, 12-byte random prefix, msgpack, no encryption):
//
//  1. channels_redis RedisChannelLayer.group_send, command for command:
//     ZREMRANGEBYSCORE on the group key, ZRANGE, a MULTI/EXEC pipeline that
//     trims every channel key, then one EVAL of the same Lua script with the
//     per-key msgpack messages (each carrying "__asgi_channel__"), the
//     capacities, time.time() as redis-py renders it and the expiry. Django
//     consumers connected through channels_redis receive it.
//  2. The Go mirror: PUBLISH on RealtimeChannel of the version-1 JSON
//     envelope json.dumps({"version": 1, "group": group, "message": message}),
//     which Go WebSocket servers consume. Like realtime_layer.py, a failed
//     mirror publish is logged and not returned.
//
// Byte equality with the Python layer is pinned by
// testdata/channels_redis_group_send.json, captured from the real layer by
// TESTS/adapters/go/channels_group_send_golden.py.
//
// Only the single-host ring is reproduced: channels_redis's consistent hash
// maps every group and channel to host 0 when one host is configured.
package channels

import (
	"context"
	"crypto/rand"
	"fmt"
	"io"
	"log/slog"
	"strconv"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"
)

// RealtimeChannel is realtime_layer.REALTIME_CHANNEL, the pub/sub channel
// of the Go mirror envelope.
const RealtimeChannel = "assozeta:realtime:v1"

// channels_redis RedisChannelLayer defaults (BE/core/settings.py sets none).
const (
	keyPrefix          = "asgi"
	expiry             = 60
	groupExpiry        = 86400
	capacity           = 100
	randomPrefixLength = 12
	asgiChannelKey     = "__asgi_channel__"
)

// groupSendLua is channels_redis 4.2.1's group_send script, whitespace
// included: EVAL sends the source text, not a digest.
const groupSendLua = "\n" +
	"                local over_capacity = 0\n" +
	"                local current_time = ARGV[#ARGV - 1]\n" +
	"                local expiry = ARGV[#ARGV]\n" +
	"                for i=1,#KEYS do\n" +
	"                    if redis.call('ZCOUNT', KEYS[i], '-inf', '+inf') < tonumber(ARGV[i + #KEYS]) then\n" +
	"                        redis.call('ZADD', KEYS[i], current_time, ARGV[i])\n" +
	"                        redis.call('EXPIRE', KEYS[i], expiry)\n" +
	"                    else\n" +
	"                        over_capacity = over_capacity + 1\n" +
	"                    end\n" +
	"                end\n" +
	"                return over_capacity\n" +
	"            "

// Layer is the Django channel layer as seen by a sender.
type Layer struct {
	client *redis.Client
	now    func() time.Time
	random io.Reader
	logger *slog.Logger
}

// NewLayer returns a Layer on client, which must point at the Redis host
// and DB of Django's CHANNEL_LAYERS. now stands for Python's time.time()
// (nil: time.Now) and random supplies the 12-byte message prefixes that
// channels_redis draws from random.getrandbits (nil: crypto/rand). A nil
// logger means slog.Default().
func NewLayer(client *redis.Client, now func() time.Time, random io.Reader, logger *slog.Logger) *Layer {
	if now == nil {
		now = time.Now
	}
	if random == nil {
		random = rand.Reader
	}
	if logger == nil {
		logger = slog.Default()
	}
	return &Layer{client: client, now: now, random: random, logger: logger}
}

// GroupSend is GoMirroredRedisChannelLayer.group_send(group, message). An
// invalid group name or message value is refused before any command. A
// channels_redis step failure is returned (Django's callers log and swallow
// it); the mirror publish then does not happen, as in Python. A mirror
// failure is only logged.
func (l *Layer) GroupSend(ctx context.Context, group string, message Dict) error {
	if err := validGroupName(group); err != nil {
		return err
	}
	if err := validateValue(message); err != nil {
		return err
	}
	if err := l.channelsGroupSend(ctx, group, message); err != nil {
		return err
	}
	if err := l.client.Do(ctx, "PUBLISH", RealtimeChannel, envelope(group, message)).Err(); err != nil {
		l.logger.Warn("Unable to mirror a group message for Go WebSockets", "group", group, "error", err)
	}
	return nil
}

// channelsGroupSend is channels_redis RedisChannelLayer.group_send.
func (l *Layer) channelsGroupSend(ctx context.Context, group string, message Dict) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	key := keyPrefix + ":group:" + group
	if err := l.client.Do(ctx, "ZREMRANGEBYSCORE", key, "0", l.secondsBefore(groupExpiry)).Err(); err != nil {
		return fmt.Errorf("channels: trim group: %w", err)
	}
	members, err := l.client.Do(ctx, "ZRANGE", key, "0", "-1").StringSlice()
	if err != nil {
		return fmt.Errorf("channels: read group: %w", err)
	}
	if len(members) == 0 {
		return nil
	}
	keys, messages, err := l.channelMessages(members, message)
	if err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	// redis-py's default pipeline is a MULTI/EXEC transaction; each key reads
	// the clock again. go-redis's TxPipeline would send lower-case MULTI/EXEC,
	// so the transaction is spelled out on a plain pipeline.
	pipe := l.client.Pipeline()
	pipe.Do(ctx, "MULTI")
	for _, channelKey := range keys {
		pipe.Do(ctx, "ZREMRANGEBYSCORE", channelKey, "0", l.secondsBefore(expiry))
	}
	exec := pipe.Do(ctx, "EXEC")
	if _, err := pipe.Exec(ctx); err != nil {
		return fmt.Errorf("channels: trim channels: %w", err)
	}
	results, err := exec.Slice()
	if err != nil {
		return fmt.Errorf("channels: trim channels: %w", err)
	}
	for _, result := range results {
		if err, ok := result.(error); ok {
			return fmt.Errorf("channels: trim channels: %w", err)
		}
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	args := make([]any, 0, 3+3*len(keys)+2)
	args = append(args, "EVAL", groupSendLua, strconv.Itoa(len(keys)))
	for _, channelKey := range keys {
		args = append(args, channelKey)
	}
	for _, payload := range messages {
		args = append(args, payload)
	}
	for range keys {
		args = append(args, strconv.Itoa(capacity))
	}
	args = append(args, pythonTimeRepr(l.now()), strconv.Itoa(expiry))
	overCapacity, err := l.client.Do(ctx, args...).Int64()
	if err != nil {
		return fmt.Errorf("channels: deliver: %w", err)
	}
	if overCapacity > 0 {
		l.logger.Info(fmt.Sprintf("%d of %d channels over capacity in group %s", overCapacity, len(members), group))
	}
	return nil
}

// channelMessages is _map_channel_keys_to_connection for one host: channel
// keys in first-seen order, each with a copy of message whose
// "__asgi_channel__" lists the group's channels stored under that key,
// serialized with its random prefix.
func (l *Layer) channelMessages(members []string, message Dict) ([]string, [][]byte, error) {
	var keys []string
	targets := map[string][]any{}
	for _, channel := range members {
		channelKey := keyPrefix + nonLocalName(channel)
		if _, seen := targets[channelKey]; !seen {
			keys = append(keys, channelKey)
		}
		targets[channelKey] = append(targets[channelKey], channel)
	}
	messages := make([][]byte, len(keys))
	for i, channelKey := range keys {
		payload := make([]byte, randomPrefixLength, randomPrefixLength+256)
		if _, err := io.ReadFull(l.random, payload); err != nil {
			return nil, nil, fmt.Errorf("channels: random prefix: %w", err)
		}
		messages[i] = appendMsgpack(payload, withAsgiChannel(message, targets[channelKey]))
	}
	return keys, messages, nil
}

// withAsgiChannel is dict(message.items()) followed by
// message["__asgi_channel__"] = channels: appended last, or replaced in
// place when the caller's message already has the key.
func withAsgiChannel(message Dict, channels []any) Dict {
	copied := make(Dict, len(message), len(message)+1)
	copy(copied, message)
	for i := range copied {
		if copied[i].Key == asgiChannelKey {
			copied[i].Value = channels
			return copied
		}
	}
	return append(copied, Item{Key: asgiChannelKey, Value: channels})
}

// nonLocalName is BaseChannelLayer.non_local_name: a process-specific name
// up to and including its first "!", any other name whole.
func nonLocalName(channel string) string {
	if i := strings.IndexByte(channel, '!'); i >= 0 {
		return channel[:i+1]
	}
	return channel
}

// secondsBefore is int(time.time()) - seconds as redis-py sends it.
func (l *Layer) secondsBefore(seconds int64) string {
	return strconv.FormatInt(int64(pythonTime(l.now()))-seconds, 10)
}
