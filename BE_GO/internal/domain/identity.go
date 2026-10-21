package domain

import "errors"

var ErrAssociationNotFound = errors.New("association not found")
var ErrInvalidPermissionState = errors.New("invalid persisted collaborator permissions")

// IdentityRequest contains untrusted context headers and an upstream route.
// Prefix stripping is owned by routing. These fields never grant scope directly.
type IdentityRequest struct{ Method, Path, SessionID, TargetID string }

// ResolvedIdentity identifies who acts and how data must be constrained. It is
// not endpoint/resource authorization: services must enforce group, instructor,
// family, nested-ID and association constraints before returning data or effects.
type ResolvedIdentity struct {
	Actor                      *Account
	Effective                  *Account
	ScopeUser                  *Account
	Collaborator               bool
	Impersonating              bool
	ImpersonationAssociationID string
	SessionID                  string
}
