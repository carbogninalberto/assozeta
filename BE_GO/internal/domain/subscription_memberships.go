package domain

import (
	"context"
	"errors"
	"time"
)

var (
	// ErrSubscriptionMembershipNotFound is get_object_or_404's
	// {'detail': 'No SubscriptionMembership matches the given query.'}.
	ErrSubscriptionMembershipNotFound = errors.New("subscription membership not found")
	// ErrSubscriptionMembershipMalformed is the bare Http404 for a pk the
	// UUIDField rejects ({'detail': 'Not found.'}).
	ErrSubscriptionMembershipMalformed = errors.New("subscription membership pk malformed")
	// ErrSubscriptionMembershipForeignReference is the tenant scope's
	// PermissionDenied for a relation to another tenant's row.
	ErrSubscriptionMembershipForeignReference = errors.New("subscription membership foreign reference")
)

// SubscriptionMembership is one application_subscriptionmembership row.
// Dates are ISO dates, Price the decimal text.
type SubscriptionMembership struct {
	ID               string
	SubscriptionID   *string
	AssociationID    string
	AssociateID      *string
	PaymentID        *string
	CreationDate     time.Time
	StartDate        *string
	EndDate          *string
	MembershipType   *string
	MembershipNumber *string
	Description      *string
	Price            *string
}

// SubscriptionMembershipFilter is get_queryset's optional subscription and
// associate_id query filters (canonical UUIDs).
type SubscriptionMembershipFilter struct {
	Subscription *string
	Associate    *string
}

// SubscriptionMembershipRow is a membership with payment.paid (the forward
// relation's base manager) and the nested documents JSON.
type SubscriptionMembershipRow struct {
	SubscriptionMembership
	Paid      *bool
	Documents string
}

// SubscriptionMembershipReference is a related row found through the
// serializer field's queryset: its association, if any.
type SubscriptionMembershipReference struct {
	AssociationID *string
}

type SubscriptionMembershipTransaction interface {
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	// Memberships lists the association's memberships through the filter;
	// with id it is the locked pk lookup.
	Memberships(ctx context.Context, association string, filter SubscriptionMembershipFilter, id *string) ([]SubscriptionMembershipRow, error)
	MembershipIDTaken(ctx context.Context, id string, exclude *string) (bool, error)
	// Association, Associate and Payment return nil when the field's
	// queryset has no such row (group is the X-Group-Id filter).
	Association(ctx context.Context, id string) (*SubscriptionMembershipReference, error)
	Associate(ctx context.Context, id string, group *string) (*SubscriptionMembershipReference, error)
	Payment(ctx context.Context, id string, group *string) (*SubscriptionMembershipReference, error)
	PaymentPaid(ctx context.Context, id string) (*bool, error)
	MembershipDocuments(ctx context.Context, id string) (string, error)
	// CreateMembership inserts the row with its create entry.
	CreateMembership(ctx context.Context, row SubscriptionMembership, audit LoginAuditContext, at time.Time) error
	SaveMembership(ctx context.Context, row SubscriptionMembership, changes map[string][2]string, audit LoginAuditContext, at time.Time) error
	DeleteMembership(ctx context.Context, id string, audit LoginAuditContext, at time.Time) error
	Commit() error
	Rollback() error
}

type SubscriptionMembershipStore interface {
	BeginSubscriptionMembership(context.Context) (SubscriptionMembershipTransaction, error)
}
