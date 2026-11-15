package repository

import (
	"bytes"
	"context"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// The expected bytes are pickle.dumps(value, 5) from CPython 3.13 (django-redis
// PickleSerializer with pickle.DEFAULT_PROTOCOL).
func TestPicklePythonFloatsMatchesCPython(t *testing.T) {
	for _, item := range []struct {
		values []float64
		want   []byte
	}{
		{[]float64{1.5}, []byte("\x80\x05\x95\r\x00\x00\x00\x00\x00\x00\x00]\x94G?\xf8\x00\x00\x00\x00\x00\x00a.")},
		{[]float64{1.5, 2.5}, []byte("\x80\x05\x95\x17\x00\x00\x00\x00\x00\x00\x00]\x94(G?\xf8\x00\x00\x00\x00\x00\x00G@\x04\x00\x00\x00\x00\x00\x00e.")},
	} {
		got := picklePythonFloats(item.values)
		if !bytes.Equal(got, item.want) {
			t.Fatalf("pickle(%v) = %q", item.values, got)
		}
		decoded, err := decodeDjangoSession(context.Background(), got)
		if err != nil {
			t.Fatal(err)
		}
		list, ok := decoded.([]any)
		if !ok || len(list) != len(item.values) || list[0] != item.values[0] {
			t.Fatalf("round trip = %#v", decoded)
		}
	}
}

// TestThrottleHistoryReadsDjangoCacheValues pins what SimpleRateThrottle reads
// from the cache entry: the timestamps of a list of numbers, where an integer or
// boolean entry counts as its numeric value, and an error for every other value
// (which Django answers with its error page before the view runs). The bytes are
// pickle.dumps(value, 4) from CPython 3.13, the runtime of BE.
func TestThrottleHistoryReadsDjangoCacheValues(t *testing.T) {
	for _, item := range []struct {
		name string
		raw  []byte
		want []float64
		bad  bool
	}{
		{"floats", []byte("\x80\x04\x95\x17\x00\x00\x00\x00\x00\x00\x00]\x94(G?\xf8\x00\x00\x00\x00\x00\x00G@\x04\x00\x00\x00\x00\x00\x00e."), []float64{1.5, 2.5}, false},
		{"empty list", []byte("\x80\x04]\x94."), []float64{}, false},
		{"integers", []byte("\x80\x04\x95\t\x00\x00\x00\x00\x00\x00\x00]\x94(K\x01K\x02e."), []float64{1, 2}, false},
		{"booleans", []byte("\x80\x04\x95\x07\x00\x00\x00\x00\x00\x00\x00]\x94(\x88\x89e."), []float64{1, 0}, false},
		{"string", []byte("\x80\x04\x95\x11\x00\x00\x00\x00\x00\x00\x00\x8c\rnot a history\x94."), nil, true},
		{"dict", []byte("\x80\x04\x95\n\x00\x00\x00\x00\x00\x00\x00}\x94\x8c\x01a\x94K\x01s."), nil, true},
		{"none", []byte("\x80\x04N."), nil, true},
		{"list with a string", []byte("\x80\x04\x95\x12\x00\x00\x00\x00\x00\x00\x00]\x94(\x8c\x01x\x94G?\xf8\x00\x00\x00\x00\x00\x00e."), nil, true},
		{"not a pickle", []byte("not a pickle"), nil, true},
	} {
		decoded, err := decodeDjangoSession(context.Background(), item.raw)
		var history []float64
		if err == nil {
			history, err = throttleHistory(decoded)
		}
		if item.bad {
			if err == nil {
				t.Fatalf("%s: want an error, got %v", item.name, history)
			}
			continue
		}
		if err != nil {
			t.Fatalf("%s: %v", item.name, err)
		}
		if !equalFloats(history, item.want) {
			t.Fatalf("%s = %v, want %v", item.name, history, item.want)
		}
	}
}

func equalFloats(got, want []float64) bool {
	if len(got) != len(want) {
		return false
	}
	for index := range got {
		if got[index] != want[index] {
			return false
		}
	}
	return true
}

// A cached entry Django answers with its error page is not a "not ported" input:
// Go serves the request itself.
func TestThrottleHistoryValueIsNotARefusal(t *testing.T) {
	if errors.Is(errThrottleHistoryValue, refusal.ErrNotPorted) {
		t.Fatal("errThrottleHistoryValue must not be a refusal sentinel")
	}
}
