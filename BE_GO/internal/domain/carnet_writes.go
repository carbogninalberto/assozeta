package domain

import (
	"context"
	"errors"
	"time"
)

type CarnetDeleteStore interface {
	BeginCarnetWrite(context.Context) (CarnetWriteTransaction, error)
}

type CarnetWriteTransaction interface {
	// OwnedCarnetForUpdate locks Carnet.get(carnet_id=id, sport_association__user=owner)
	// within the impersonation association; ErrCarnetNotFound when absent.
	OwnedCarnetForUpdate(ctx context.Context, id, owner string, scope *string) (association string, err error)
	// DeleteCarnet runs the collector cascade with one audit entry per deleted
	// carnet subscription and the carnet; actorAssociation indexes the
	// subscriptions (nil: no index).
	DeleteCarnet(ctx context.Context, id, association string, actorAssociation *string, audit LoginAuditContext, at time.Time) error
	ActiveAssociationOf(ctx context.Context, user string) (*string, error)
	Commit() error
	Rollback() error
}

// CarnetEditable is a locked carnet's stored update fields.
type CarnetEditable struct {
	AssociationID string  `db:"sport_association_id"`
	Title         *string `db:"title"`
	Description   *string `db:"description"`
	Lessons       int64   `db:"lessons_number"`
	Fee           string  `db:"fee"`
	Public        bool    `db:"public"`
}

type CarnetUpdateWrite struct {
	ID, Title, Description, Fee string
	Lessons                     int64
	Public                      bool
	Changes                     map[string][2]string
	Context                     LoginAuditContext
	At                          time.Time
}

type CarnetUpdateTransaction interface {
	CarnetForEdit(ctx context.Context, id, owner string, scope *string) (CarnetEditable, error)
	UpdateCarnet(ctx context.Context, write CarnetUpdateWrite, association string) error
	Commit() error
	Rollback() error
}

type CarnetUpdateStore interface {
	BeginCarnetUpdate(context.Context) (CarnetUpdateTransaction, error)
}

// ErrPaymentCascadeUnsupported refuses a payment delete whose pre_delete
// signal would also delete course installments (not ported).
var ErrPaymentCascadeUnsupported = errors.New("payment delete cascade unsupported")

type CarnetHoldingPayment struct {
	PaymentID *string `db:"payment_id"`
	Paid      *bool   `db:"paid"`
}

type CarnetUnassignTransaction interface {
	// HoldingForUnassign locks a carnet subscription of the owner's subscriptions.
	HoldingForUnassign(ctx context.Context, holding, owner string, scope *string) (CarnetHoldingPayment, error)
	DeleteUnpaidPayment(ctx context.Context, payment string, audit LoginAuditContext, at time.Time) error
	DeleteHolding(ctx context.Context, holding string, actorAssociation *string, audit LoginAuditContext, at time.Time) error
	ActiveAssociationOf(ctx context.Context, user string) (*string, error)
	Commit() error
	Rollback() error
}

type CarnetUnassignStore interface {
	BeginCarnetUnassign(context.Context) (CarnetUnassignTransaction, error)
}

// ErrCarnetSubscriptionNotFound is {'msg': 'Carnet subscription not found.'}.
var ErrCarnetSubscriptionNotFound = errors.New("carnet subscription not found")

type CarnetToggleTransaction interface {
	// HoldingForToggle locks the owner's carnet subscription and returns its
	// flag and subscription association.
	HoldingForToggle(ctx context.Context, holding, owner string, scope *string) (disabled bool, association string, err error)
	SetHoldingDisabled(ctx context.Context, holding string, disabled bool, association string, audit LoginAuditContext, at time.Time, changed bool) error
	Commit() error
	Rollback() error
}

type CarnetToggleStore interface {
	BeginCarnetToggle(context.Context) (CarnetToggleTransaction, error)
}

type CarnetLinkTransaction interface {
	HoldingForToggle(ctx context.Context, holding, owner string, scope *string) (disabled bool, association string, err error)
	// RemoveCourseLink is course_subscription.remove(id): only live course
	// subscriptions are matched (the default manager); no audit entry.
	RemoveCourseLink(ctx context.Context, holding, course string) error
	Commit() error
	Rollback() error
}

type CarnetLinkStore interface {
	BeginCarnetLink(context.Context) (CarnetLinkTransaction, error)
}

type CarnetMetaTransaction interface {
	// HoldingMeta locks the owner's carnet subscription and returns its meta
	// JSON text (nil for SQL NULL) and subscription association.
	HoldingMeta(ctx context.Context, holding, owner string, scope *string) (meta *string, association string, err error)
	SaveHoldingMeta(ctx context.Context, holding, meta, association string, changes map[string][2]string, audit LoginAuditContext, at time.Time) error
	Commit() error
	Rollback() error
}

type CarnetMetaStore interface {
	BeginCarnetMeta(context.Context) (CarnetMetaTransaction, error)
}
