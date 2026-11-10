// Package refusal marks the inputs Go deliberately does not serve yet (the
// "not ported" sentinels of every family). Each family keeps its own sentinel,
// so errors.Is(err, family.ErrX) is unchanged, and every sentinel also matches
// ErrNotPorted, so one check finds all of them. Handlers refuse such inputs
// before any write; internal/handoff replays them to Django when configured.
package refusal

import "errors"

// ErrNotPorted matches every sentinel created with New.
var ErrNotPorted = errors.New("not ported")

// Error is a refusal sentinel. Its message is a constant chosen by the family
// (never request data), so it is safe to log as the refusal reason.
type Error struct{ reason string }

func (e *Error) Error() string { return e.reason }

// Is makes every refusal sentinel match ErrNotPorted.
func (e *Error) Is(target error) bool { return target == ErrNotPorted }

// New returns a new refusal sentinel. Compare it with errors.Is as usual.
func New(reason string) error { return &Error{reason: reason} }

// Reason reports whether err wraps a refusal sentinel and, if so, the first
// sentinel's constant message.
func Reason(err error) (string, bool) {
	var refused *Error
	if errors.As(err, &refused) {
		return refused.reason, true
	}
	return "", false
}
