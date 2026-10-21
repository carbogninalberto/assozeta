package domain

import (
	"context"
	"errors"
)

var ErrLoginRateCounterMissing = errors.New("login rate counter expired during increment")

// LoginRateCounter increments an existing Django cache counter or creates it
// with the fixed 65-second expiration. Incrementing never extends its lifetime.
type LoginRateCounter interface {
	IncrementLoginRate(context.Context, string) (int64, error)
}
