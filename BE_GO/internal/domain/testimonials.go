package domain

import (
	"context"
	"time"
)

type TestimonialWrite struct {
	ID, AssociationID, Text string
	Score                   float64
}

// AssociationReviewedAudit is auditlog's update entry for the reviewed flag on
// SportAssociation, indexed by its own registered (self) resolver.
type AssociationReviewedAudit struct {
	AssociationID string
	Context       LoginAuditContext
	At            time.Time
}

type TestimonialStore interface {
	BeginTestimonialMutations(context.Context) (TestimonialTransaction, error)
}

type TestimonialTransaction interface {
	AssociationForOwner(context.Context, string, *string) (string, error)
	InsertTestimonial(context.Context, TestimonialWrite) error
	// MarkAssociationReviewed sets reviewed and reports whether it was false.
	MarkAssociationReviewed(ctx context.Context, association string) (bool, error)
	AppendAssociationReviewedAudit(context.Context, AssociationReviewedAudit) error
	Commit() error
	Rollback() error
}
