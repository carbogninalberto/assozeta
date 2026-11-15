// Package refusal is the fixture's copy of BE_GO/internal/refusal: the
// analyzer recognizes refusal.New from the module's internal/refusal package.
package refusal

import "errors"

// ErrNotPorted matches every sentinel created with New.
var ErrNotPorted = errors.New("not ported")

// Error is a refusal sentinel.
type Error struct{ reason string }

func (e *Error) Error() string { return e.reason }

// Is makes every refusal sentinel match ErrNotPorted.
func (e *Error) Is(target error) bool { return target == ErrNotPorted }

// New returns a new refusal sentinel.
func New(reason string) error { return &Error{reason: reason} }
