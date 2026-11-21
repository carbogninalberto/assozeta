//go:build handoff

package main

// Phase 2 handoff contract (t-p2): the Go API's resident startup effects can be
// switched off independently, so a deployment controller can start the API
// privately (switch preflight) without delivering anything.
//
// Contract pinned here:
//   - type startupEffects struct{ WhatsAppOutbox bool }
//   - func startupEffectsFrom(getenv func(string) string) (startupEffects, error)
//     ASSOZETA_GO_WHATSAPP_OUTBOX unset, "" or "1": the embedded WhatsApp
//     outbox loop runs (unchanged behaviour); "0": run() never starts it; any
//     other value is an error whose text names ASSOZETA_GO_WHATSAPP_OUTBOX.
//   - run() validates the startup switches before it loads configuration or
//     opens any connection: an invalid value stops startup with that error.
//   - The Django HTTP handoff stays governed by DJANGO_HANDOFF_URL (empty
//     disables it); production Compose renders it empty for api-go
//     (TESTS/handoff/test_p2_process_sets.py).
//
// Run through TESTS/handoff/test_p2_go_startup.py (build tag handoff), so the
// red contract never breaks `go test ./...` in the existing CI jobs.

import (
	"strings"
	"testing"
)

func TestHandoffStartupEffectsGate(t *testing.T) {
	for _, tc := range []struct {
		name    string
		value   *string
		outbox  bool
		invalid bool
	}{
		{name: "unset keeps the poller", outbox: true},
		{name: "empty keeps the poller", value: handoffSwitch(""), outbox: true},
		{name: "1 runs the poller", value: handoffSwitch("1"), outbox: true},
		{name: "0 gates the poller off", value: handoffSwitch("0"), outbox: false},
		{name: "true is not a switch value", value: handoffSwitch("true"), invalid: true},
		{name: "padded zero is not a switch value", value: handoffSwitch(" 0"), invalid: true},
		{name: "off is not a switch value", value: handoffSwitch("off"), invalid: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			getenv := func(key string) string {
				if key == "ASSOZETA_GO_WHATSAPP_OUTBOX" && tc.value != nil {
					return *tc.value
				}
				return ""
			}
			effects, err := startupEffectsFrom(getenv)
			if tc.invalid {
				if err == nil || !strings.Contains(err.Error(), "ASSOZETA_GO_WHATSAPP_OUTBOX") {
					t.Fatalf("invalid switch value accepted or unnamed: effects=%+v err=%v", effects, err)
				}
				return
			}
			if err != nil {
				t.Fatalf("valid switch value rejected: %v", err)
			}
			if effects.WhatsAppOutbox != tc.outbox {
				t.Fatalf("WhatsAppOutbox = %v, want %v", effects.WhatsAppOutbox, tc.outbox)
			}
		})
	}
}

func TestHandoffInvalidStartupSwitchStopsBeforeConfiguration(t *testing.T) {
	// No database, Redis or deployment configuration exists in this process:
	// the switch must be the first thing run() refuses.
	for _, key := range []string{"DBNAME", "DBUSER", "DBHOST", "DBTYPE", "SECRET_KEY"} {
		t.Setenv(key, "")
	}
	t.Setenv("ASSOZETA_GO_WHATSAPP_OUTBOX", "maybe")
	err := run()
	if err == nil || !strings.Contains(err.Error(), "ASSOZETA_GO_WHATSAPP_OUTBOX") {
		t.Fatalf("run() with an invalid ASSOZETA_GO_WHATSAPP_OUTBOX returned %v", err)
	}
}

func handoffSwitch(value string) *string { return &value }
