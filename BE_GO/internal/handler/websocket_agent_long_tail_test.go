package handler

import (
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// Item 7: `count <= ”` raises inside receive_json (the connection crashes);
// a number big.Rat cannot read or another type is an application state.
func TestAgentLessEqualShapes(t *testing.T) {
	for _, item := range []struct {
		limit any
		ok    bool
		crash bool
		state bool
	}{
		{json.Number("10"), true, false, false},
		{json.Number("3"), false, false, false},
		{int64(5), true, false, false},
		{true, false, false, false},
		{"", false, true, false},
		{"10", false, false, true},
		{[]any{}, false, false, true},
		{json.Number("NaN"), false, false, true},
	} {
		allowed, err := agentLessEqual(5, item.limit)
		if err == nil {
			if item.crash || item.state || allowed != item.ok {
				t.Fatalf("%v: got %v, %v", item.limit, allowed, err)
			}
			continue
		}
		if item.crash != errors.Is(err, errCrash) {
			t.Fatalf("%v: errCrash = %v (%v)", item.limit, errors.Is(err, errCrash), err)
		}
		if item.state != errors.Is(err, refusal.ErrAppState) {
			t.Fatalf("%v: AppState = %v (%v)", item.limit, errors.Is(err, refusal.ErrAppState), err)
		}
		if errors.Is(err, refusal.ErrNotPorted) {
			t.Fatalf("%v: refusal leaked: %v", item.limit, err)
		}
	}
}

// Item 7: asyncio.wait_for's three outcomes for ws_timeout.
func TestAgentSecondsShapes(t *testing.T) {
	for _, item := range []struct {
		value any
		want  time.Duration
		mode  agentTimeoutMode
		state bool
	}{
		{json.Number("1.5"), 1500 * time.Millisecond, agentTimeoutWait, false},
		{int64(2), 2 * time.Second, agentTimeoutWait, false},
		{json.Number("0"), 0, agentTimeoutExpired, false},
		{int64(-5), 0, agentTimeoutExpired, false},
		/* an over-range literal is Python's float inf: wait forever */
		{json.Number("1e400"), time.Duration(1<<63 - 1), agentTimeoutWait, false},
		{int64(1 << 62), time.Duration(1<<63 - 1), agentTimeoutWait, false},
		{"", 0, agentTimeoutTypeError, false},
		{"10", 0, agentTimeoutWait, true},
		{[]any{}, 0, agentTimeoutWait, true},
		{json.Number("NaN"), 0, agentTimeoutWait, true},
	} {
		got, mode, err := agentSeconds(item.value)
		if item.state {
			if err == nil || !errors.Is(err, refusal.ErrAppState) {
				t.Fatalf("%v: want AppState, got %v", item.value, err)
			}
			if errors.Is(err, refusal.ErrNotPorted) {
				t.Fatalf("%v: refusal leaked: %v", item.value, err)
			}
			continue
		}
		if err != nil || got != item.want || mode != item.mode {
			t.Fatalf("%v: got %v, %v, %v", item.value, got, mode, err)
		}
	}
}
