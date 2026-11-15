// Package cases has one refusal shape per analyzer case: a direct return, a
// wrapped %w, an interface dispatch, and a sentinel used only with errors.Is.
package cases

import (
	"errors"
	"fmt"

	"fixture/refusalreach/internal/refusal"
)

var (
	ErrDirect    = refusal.New("direct sentinel")
	ErrWrapped   = refusal.New("wrapped sentinel")
	ErrInterface = refusal.New("interface sentinel")
	ErrOnlyCheck = refusal.New("only errors.Is")
)

// Direct returns a sentinel unchanged.
func Direct() error { return ErrDirect }

// Wrapped returns a sentinel through fmt.Errorf %w.
func Wrapped() error { return fmt.Errorf("wrapped: %w", ErrWrapped) }

// Runner is the interface dispatched through.
type Runner interface {
	Run() error
}

type implementation struct{}

func (implementation) Run() error { return ErrInterface }

// Impl returns a concrete Runner.
func Impl() Runner { return implementation{} }

// Dispatch calls the interface method.
func Dispatch(runner Runner) error { return runner.Run() }

// Check only compares a sentinel, so no refusal is returned here.
func Check(err error) bool { return errors.Is(err, ErrOnlyCheck) }
