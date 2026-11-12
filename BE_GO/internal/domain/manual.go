package domain

import "context"

// ManualAvailability discovers offline evidence or a configured lazy publisher.
// Availability does not validate reader visibility or permit unverified assets.
type ManualAvailability interface {
	Available(context.Context) (bool, error)
}

// ManualToolSnapshot contains only live ownership facts, never integration
// credentials. An empty ActiveUserID means missing, disabled or deleted user.
type ManualToolSnapshot struct {
	SelfHosted           bool   `db:"self_hosted"`
	PrimaryAssociationID string `db:"primary_association_id"`
	PrimaryOwnerID       string `db:"primary_owner_id"`
	ActiveUserID         string `db:"active_user_id"`
}
