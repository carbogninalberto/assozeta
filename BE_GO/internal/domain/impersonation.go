package domain

import "errors"

var ErrInvalidImpersonation = errors.New("invalid impersonation session")
var ErrImpersonationNotFound = errors.New("impersonation session expired or revoked")

// ImpersonationSession is only a cache record, not proof of current eligibility.
// Resolve its actor/target/association against persisted state on every request.
type ImpersonationSession struct {
	ActorID       string `json:"actor_id"`
	TargetID      string `json:"target_id"`
	AssociationID string `json:"association_id"`
}
