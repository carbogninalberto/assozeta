package celeryworker

import (
	"os/exec"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
)

// TestEffectiveLimit is `header or task default` (Request.time_limits and
// execute_using_pool): a falsy header value falls back to the default.
func TestEffectiveLimit(t *testing.T) {
	cases := []struct {
		header   any
		fallback celery.Number
		want     celery.Number
		seconds  float64
	}{
		{celery.Number("30"), "3", "30", 30},
		{nil, "3", "3", 3},
		{celery.Number("0"), "1", "1", 1},
		{celery.Number("0.0"), "", "", 0},
		{celery.Number("2.5"), "", "2.5", 2.5},
		{nil, "", "", 0},
	}
	for _, c := range cases {
		got, seconds := effectiveLimit(c.header, c.fallback)
		if got != c.want || seconds != c.seconds {
			t.Fatalf("effectiveLimit(%v, %q) = %q, %v", c.header, c.fallback, got, seconds)
		}
	}
	if !validTimeLimits([]any{nil, celery.Number("3300")}) || validTimeLimits([]any{"3600", nil}) {
		t.Fatal("time limit validation")
	}
}

// TestHumanStatus is billiard.common.human_status.
func TestHumanStatus(t *testing.T) {
	cmd := exec.Command("sh", "-c", "kill -9 $$")
	_ = cmd.Run()
	if got := humanStatus(cmd.ProcessState); got != "signal 9 (SIGKILL)" {
		t.Fatalf("killed: %q", got)
	}
	cmd = exec.Command("sh", "-c", "exit 3")
	_ = cmd.Run()
	if got := humanStatus(cmd.ProcessState); got != "exitcode 3" {
		t.Fatalf("exit: %q", got)
	}
}

// TestPreforkOptionsNeedPrefork: acks_late and time limits are only applied
// by the prefork execution.
func TestPreforkOptionsNeedPrefork(t *testing.T) {
	transport := &celery.Transport{Queue: celery.GoQueue}
	options := Options{Concurrency: 1, HandoffQueue: celery.DefaultQueue}
	if _, err := New(transport, map[string]Task{"x": {AcksLate: true}}, nil, nil, options, nil); err == nil {
		t.Fatal("acks_late without prefork accepted")
	}
	if _, err := New(transport, map[string]Task{"x": {Prefork: true}}, nil, nil, options, nil); err == nil {
		t.Fatal("prefork without a child launcher accepted")
	}
	options.Child = ChildCommand("--prefork-child")
	if _, err := New(transport, map[string]Task{"x": {Prefork: true, AcksLate: true, TimeLimit: "3600"}}, nil, nil, options, nil); err != nil {
		t.Fatal(err)
	}
}
