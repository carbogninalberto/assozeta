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

// CarnetTopupSource is the topped-up carnet subscription with its carnet and
// subscription fields; Subscription* are nil without a subscription.
type CarnetTopupSource struct {
	UserID                  *string `db:"user_id"`
	CarnetID                string  `db:"carnet_id"`
	CarnetAssociation       *string `db:"carnet_association"`
	Fee                     string  `db:"fee"`
	Lessons                 int64   `db:"lessons_number"`
	SubscriptionID          *string `db:"subscription_id"`
	SubscriptionUser        *string `db:"subscription_user"`
	SubscriptionAssociate   *string `db:"subscription_associate"`
	SubscriptionAssociation *string `db:"subscription_association"`
}

type CarnetTopupWrite struct {
	PaymentID, HoldingID string
	Source               CarnetTopupSource
	CategoryID, Account  *string
	Meta                 string
	Context              LoginAuditContext
	At                   time.Time
}

type CarnetTopupTransaction interface {
	// TopupSource gets CarnetSubscription.objects.get(id) and, for an owner,
	// subscription__sport_association__user=owner; ErrCarnetSubscriptionNotFound.
	TopupSource(ctx context.Context, holding string, owner *string) (CarnetTopupSource, error)
	DefaultPaymentCategory(ctx context.Context) (id, name *string, err error)
	// DefaultBankAccount is Payment.get_default_bank_account(association).
	DefaultBankAccount(ctx context.Context, association *string) (*string, error)
	// CreateTopup inserts the payment and carnet subscription with their
	// create audit entries and links the source's live course subscriptions.
	CreateTopup(ctx context.Context, source string, write CarnetTopupWrite) error
	Commit() error
	Rollback() error
}

type CarnetTopupStore interface {
	BeginCarnetTopup(context.Context) (CarnetTopupTransaction, error)
}
