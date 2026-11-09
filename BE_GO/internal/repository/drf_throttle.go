package repository

import (
	"context"
	"encoding/binary"
	"errors"
	"math"
	"time"

	"github.com/redis/go-redis/v9"
)

// DRFThrottleCache stores SimpleRateThrottle histories exactly as Django's
// django-redis cache does: key ':1:<cache key>', the value a pickled
// (protocol 5) list of float timestamps, newest first, expiring after the
// throttle duration.
type DRFThrottleCache struct{ client *redis.Client }

func NewDRFThrottleCache(client *redis.Client) (*DRFThrottleCache, error) {
	if client == nil || client.Options().DB != 1 {
		return nil, errors.New("throttle cache requires Django cache database 1")
	}
	return &DRFThrottleCache{client: client}, nil
}

var errThrottleHistory = errors.New("unsupported throttle history value")

// History is cache.get(key, []).
func (r *DRFThrottleCache) History(ctx context.Context, key string) ([]float64, error) {
	raw, err := r.client.Get(ctx, ":1:"+key).Bytes()
	if errors.Is(err, redis.Nil) {
		return []float64{}, nil
	}
	if err != nil {
		return nil, err
	}
	value, err := decodeDjangoSession(ctx, raw)
	if err != nil {
		return nil, errThrottleHistory
	}
	items, ok := value.([]any)
	if !ok {
		return nil, errThrottleHistory
	}
	history := make([]float64, 0, len(items))
	for _, item := range items {
		number, ok := item.(float64)
		if !ok {
			return nil, errThrottleHistory
		}
		history = append(history, number)
	}
	return history, nil
}

// Store is cache.set(key, history, duration).
func (r *DRFThrottleCache) Store(ctx context.Context, key string, history []float64, duration time.Duration) error {
	return r.client.Set(ctx, ":1:"+key, picklePythonFloats(history), duration).Err()
}

// picklePythonFloats is pickle.dumps(list_of_floats, 5) for lists of at most
// 1000 items (a single APPENDS batch).
func picklePythonFloats(values []float64) []byte {
	body := []byte{']', 0x94}
	switch {
	case len(values) == 1:
		body = appendPickleFloat(body, values[0])
		body = append(body, 'a')
	case len(values) > 1:
		body = append(body, '(')
		for _, value := range values {
			body = appendPickleFloat(body, value)
		}
		body = append(body, 'e')
	}
	body = append(body, '.')
	out := []byte{0x80, 5}
	if len(body) >= 4 {
		out = append(out, 0x95)
		out = binary.LittleEndian.AppendUint64(out, uint64(len(body)))
	}
	return append(out, body...)
}

func appendPickleFloat(out []byte, value float64) []byte {
	return binary.BigEndian.AppendUint64(append(out, 'G'), math.Float64bits(value))
}
