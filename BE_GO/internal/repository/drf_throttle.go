package repository

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"math"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"
)

// DRFThrottleCache stores SimpleRateThrottle histories exactly as Django's
// django-redis cache does: key ':1:<cache key>', the value a pickled list of
// float timestamps, newest first, expiring after the throttle duration.
type DRFThrottleCache struct{ client *redis.Client }

func NewDRFThrottleCache(client *redis.Client) (*DRFThrottleCache, error) {
	if client == nil || client.Options().DB != 1 {
		return nil, errors.New("throttle cache requires Django cache database 1")
	}
	return &DRFThrottleCache{client: client}, nil
}

// errThrottleHistoryValue is a cached entry that is not a history of request
// timestamps. Django's SimpleRateThrottle raises on it (`history[-1] <= ...`,
// `len(history)`) and the request fails before the view runs; the handlers
// answer that with the error page, as Django does.
var errThrottleHistoryValue = errors.New("cached throttle history is not a list of numbers")

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
		return nil, err
	}
	return throttleHistory(value)
}

// throttleHistory reads the value cache.get returned as the history DRF uses:
// a list of timestamps. Django compares and counts the entries of that list and
// inserts its own float, so an integer or boolean entry counts like a float;
// any other value is the exception described on errThrottleHistoryValue.
func throttleHistory(value any) ([]float64, error) {
	items, ok := value.([]any)
	if !ok {
		return nil, errThrottleHistoryValue
	}
	history := make([]float64, 0, len(items))
	for _, item := range items {
		number, err := throttleTimestamp(item)
		if err != nil {
			return nil, err
		}
		history = append(history, number)
	}
	return history, nil
}

// throttleTimestamp is one entry of the history as the number Django compares:
// floats as they are, integers and booleans as their numeric value.
func throttleTimestamp(item any) (float64, error) {
	switch value := item.(type) {
	case float64:
		return value, nil
	case json.Number:
		// A Python int; its text is the exact value the decoder read.
		number, err := strconv.ParseFloat(value.String(), 64)
		if err != nil || math.IsInf(number, 0) {
			return 0, errThrottleHistoryValue
		}
		return number, nil
	case bool:
		if value {
			return 1, nil
		}
		return 0, nil
	}
	return 0, errThrottleHistoryValue
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
