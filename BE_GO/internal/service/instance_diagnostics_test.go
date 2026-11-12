package service

import (
	"encoding/hex"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/instanceprobe"
)

// pickle.dumps(value, pickle.DEFAULT_PROTOCOL) from the production interpreter.
func TestDiagnosticsHeartbeatValues(t *testing.T) {
	for _, c := range []struct {
		hex    string
		value  float64
		number bool
		fails  bool
	}{
		{"8004950a000000000000004741dabcb0515000002e", 1794294085.25, true, false},
		{"80044b052e", 5, true, false},
		{"80049504000000000000004d2c012e", 300, true, false},
		{"80049506000000000000004a701101002e", 70000, true, false},
		{"8004882e", 1, true, false},
		{"80044e2e", 0, false, false},
		{"80049505000000000000008c0178942e", 0, false, false},
		{"80049506000000000000005d944b01612e", 0, false, true},
		{hex.EncodeToString([]byte(" 1_794_294_085\n")), 1794294085, true, false},
		{hex.EncodeToString([]byte("12.5")), 0, false, true},
	} {
		raw, _ := hex.DecodeString(c.hex)
		value, number, err := djangoCacheNumber(raw)
		if (err != nil) != c.fails || (!c.fails && (number != c.number || value != c.value)) {
			t.Errorf("%s: %v %v %v", c.hex, value, number, err)
		}
	}
}

func TestDiagnosticsPong(t *testing.T) {
	pong := celery.Dict{{Key: "a@x", Value: celery.Dict{{Key: "ok", Value: "pong"}}}}
	pang := celery.Dict{{Key: "a@x", Value: celery.Dict{{Key: "ok", Value: "pang"}}}}
	for _, c := range []struct {
		replies []any
		pong    bool
		fails   bool
	}{
		{nil, false, false},
		{[]any{pong}, true, false},
		{[]any{pang}, false, false},
		{[]any{pong, pang}, false, false}, // the same node: the later answer replaces it
		{[]any{celery.Dict{}}, false, false},
		{[]any{[]any{}}, false, true},
		{[]any{celery.Dict{{Key: "a@x", Value: "pong"}}}, false, true},
	} {
		got, err := diagnosticsPong(c.replies)
		if got != c.pong || (err != nil) != c.fails {
			t.Errorf("%v: %v %v", c.replies, got, err)
		}
	}
}

func TestDiagnosticsSanitizedAndURL(t *testing.T) {
	long := make([]rune, 701)
	for i := range long {
		long[i] = 'è'
	}
	if sanitizedResult(instanceprobe.Result{Status: "passed", Level: "functional", Message: string(long)}) ||
		sanitizedResult(instanceprobe.Result{Status: "ok", Level: "functional", Message: ""}) ||
		!sanitizedResult(instanceprobe.Result{Status: "warning", Level: "connectivity", Message: string(long[:700])}) {
		t.Fatal("sanitize")
	}
	for target, valid := range map[string]bool{"https://example.org": true, "HTTP://host:8000/x": true, "https://u:p@host": false,
		"ftp://host": false, "https://": false, "example.org": false, "http://[::1]:8000": true} {
		if _, ok := diagnosticURL(target); ok != valid {
			t.Errorf("%s: %v", target, ok)
		}
	}
}
