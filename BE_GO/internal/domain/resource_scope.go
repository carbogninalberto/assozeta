package domain

import "errors"

var ErrInvalidResourceScope = errors.New("invalid resource scope")
var ErrInvalidResourceBatch = errors.New("invalid resource reference batch")

// ResourceScope is supplied by trusted services after identity resolution. It
// must never be populated from request role/association headers. GroupID only
// narrows the existing association/family boundary; it never grants ownership.
// An empty GroupID means no selected-group filter, matching Django's managers.
type ResourceScope struct {
	AssociationID string
	EffectiveID   string
	Role          int
	GroupID       string
}
