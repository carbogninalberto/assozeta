package service

import (
	"encoding/json"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/instanceprobe"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// A runner reason that is not a string is str() of the JSON value, as in
// `UpdaterUnavailable(runner_status.get('reason') or default)`.
func TestTextOrIsPythonStrOfOtherValues(t *testing.T) {
	for _, tc := range []struct {
		value any
		want  string
	}{
		{json.Number("5"), "5"},
		{true, "True"},
		{[]any{json.Number("1"), "a"}, "[1, 'a']"},
		{domain.RequestObject{{Name: "a", Value: nil}}, "{'a': None}"},
		{"", "fallback"},
	} {
		got, err := textOr(tc.value, "fallback")
		if err != nil || got != tc.want {
			t.Errorf("textOr(%#v) = %q, %v; want %q", tc.value, got, err, tc.want)
		}
	}
}

// Stored shapes only the application writes are app-state errors: the 500
// page, never a handoff.
func TestInstanceAdminUnexpectedIsAppState(t *testing.T) {
	err := unexpected("stored value is not an object")
	if !errors.Is(err, refusal.ErrAppState) || errors.Is(err, refusal.ErrNotPorted) {
		t.Fatalf("unexpected() = %v: want an app-state error that is not a refusal", err)
	}
	if reason, ok := refusal.AppStateReason(err); !ok || reason != "instance administration: unexpected application state" {
		t.Fatalf("reason = %q", reason)
	}
	wrapped := unexpectedErr("cached release catalog", errors.New("decode"))
	if !errors.Is(wrapped, domain.ErrInstanceAdminUnexpected) {
		t.Fatalf("unexpectedErr = %v", wrapped)
	}
}

// X2: a feed Go does not read answers Django's provider-failure texts.
func TestReleaseNotReadable(t *testing.T) {
	if got := instanceprobe.ReleaseNotReadable(instanceprobe.ErrReleaseUnread).Message; got != "Invalid release catalog response." {
		t.Error(got)
	}
	if got := instanceprobe.ReleaseNotReadable(instanceprobe.ErrUnreadResponse).Message; got != "Release service is unavailable. Please try again later." {
		t.Error(got)
	}
}
