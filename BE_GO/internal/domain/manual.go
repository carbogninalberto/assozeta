package domain

import "context"

// ManualAvailability discovers offline evidence or a configured lazy publisher.
// Availability does not validate reader visibility or permit unverified assets.
type ManualAvailability interface {
	Available(context.Context) (bool, error)
}
