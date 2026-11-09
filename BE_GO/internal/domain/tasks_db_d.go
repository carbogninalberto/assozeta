package domain

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"
)

// Periodic Stripe and membership tasks of BE/application/tasks.py.
const (
	JobChangeTypeOfStripePayments = "change_type_of_stripe_payments"
	JobGenerateCouponIfNotExists  = "generate_coupon_if_not_exists"
	JobRenewMembershipsPayments   = "renew_memberships_payments"
)

var (
	// ErrRenewalSupportEmail refuses a renewal run that would end with
	// Django's synchronous send_mail to DEFAULT_SUPPORT_EMAIL (the
	// instance.email_configuration backend has no Go counterpart).
	ErrRenewalSupportEmail = errors.New("renewal run would send the support e-mail")
	// ErrRenewalLocked is select_for_update(nowait=True) failing on a locked row.
	ErrRenewalLocked = errors.New("course subscription row is locked")
	// ErrRenewalMissing is CourseSubscription.DoesNotExist from the locking get().
	ErrRenewalMissing = errors.New("course subscription no longer matches")
	// ErrRenewalAttribute is an AttributeError of the renewal body (no
	// association or subscription on the locked row's relations).
	ErrRenewalAttribute = errors.New("renewal relation missing")
)

// StripeTypeCandidate is one payment of change_type_of_stripe_payments' batch.
type StripeTypeCandidate struct {
	ID       uuid.UUID `db:"payment_id"`
	IntentID string    `db:"payment_intent_id"`
}

// CouponAssociation is the in-memory SportAssociation the coupon task saves.
type CouponAssociation struct {
	ID           uuid.UUID `db:"sport_association_id"`
	Denomination string    `db:"denomination"`
}

// RenewalCandidate is a row of renew_memberships_payments' initial queryset.
type RenewalCandidate struct {
	ID              uuid.UUID  `db:"course_subscription_id"`
	BilledUntil     time.Time  `db:"billed_until"`
	BilledFrequency int        `db:"billed_frequency"`
	SubscriptionID  *uuid.UUID `db:"subscription_id"`
}

// RenewalRow is the locked CourseSubscription (select_for_update().get()).
type RenewalRow struct {
	ID              uuid.UUID  `db:"course_subscription_id"`
	CourseID        uuid.UUID  `db:"course_id"`
	BilledUntil     time.Time  `db:"billed_until"`
	BilledFrequency int        `db:"billed_frequency"`
	MembershipFee   *string    `db:"membership_fee"`
	SubscriptionID  *uuid.UUID `db:"subscription_id"`
}

// RenewalExisting is the first payment already holding the period's meta.
type RenewalExisting struct {
	ID   uuid.UUID `db:"payment_id"`
	Paid bool      `db:"paid"`
}

// RenewalRelations are the lazily loaded relations Payment.objects.create reads.
type RenewalRelations struct {
	CourseTitle   string     `db:"title"`
	AssociationID *uuid.UUID `db:"sport_association_id"`
	OwnerID       *uuid.UUID `db:"user_id"`
	HasSubject    bool       `db:"has_subscription"`
	AssociateID   *uuid.UUID `db:"associate_id"`
}

// RenewalPayment is the Payment.objects.create(...) of a new period.
type RenewalPayment struct {
	ID            uuid.UUID
	CourseSub     uuid.UUID
	Amount        string
	BilledUntil   time.Time
	Description   string
	Meta          string
	OwnerID       *uuid.UUID
	AssociateID   *uuid.UUID
	AssociationID uuid.UUID
}

type TasksDBDStore interface {
	BeginTasksDBD(context.Context, Job) (TasksDBDTransaction, error)
}

// TasksDBDTransaction is one fenced READ COMMITTED transaction; the services
// open one per Django statement or atomic block.
type TasksDBDTransaction interface {
	StripeTypeCount(ctx context.Context, cutoff time.Time) (int, error)
	StripeTypeBatch(ctx context.Context, cutoff time.Time, offset, limit int) ([]StripeTypeCandidate, error)
	MarkStripeType(ctx context.Context, ids []uuid.UUID) error

	CouponAssociations(ctx context.Context) ([]CouponAssociation, error)
	SaveCoupon(ctx context.Context, association CouponAssociation, code, coupon string, at time.Time) error

	RenewalCandidates(ctx context.Context, now time.Time) ([]RenewalCandidate, error)
	RenewalExisting(ctx context.Context, row uuid.UUID, from, until string) (*RenewalExisting, error)
	LockRenewal(ctx context.Context, id uuid.UUID) (RenewalRow, error)
	RenewalRelations(ctx context.Context, row RenewalRow) (RenewalRelations, error)
	CreateRenewalPayment(ctx context.Context, payment RenewalPayment, at time.Time) error
	AdvanceRenewal(ctx context.Context, id uuid.UUID, until time.Time) error
	AddMembershipPayment(ctx context.Context, row, payment uuid.UUID) error

	CompleteJob(ctx context.Context, id, token uuid.UUID) error
	Commit() error
	Rollback() error
}

// RenewalCacheLock is cache.add/cache.delete of renew_memberships_payments_lock
// in Django's django_redis cache.
type RenewalCacheLock interface {
	Acquire(context.Context) (bool, error)
	Release(context.Context) error
}
