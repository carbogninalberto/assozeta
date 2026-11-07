package domain

import (
	"context"
	"errors"
	"time"
)

var (
	// ErrCourseWritesNotFound is course_delete's {'msg': 'Course not found.'} (404).
	ErrCourseWritesNotFound = errors.New("course not found")
	// ErrCourseWritesForeign is PermissionDenied('User not allowed.').
	ErrCourseWritesForeign = errors.New("course of another association")
	// ErrCourseWritesUpdateNotFound is course_update's ValidationError({'msg:': 'course not found.'}).
	ErrCourseWritesUpdateNotFound = errors.New("course to update not found")
	// ErrCourseWritesBodyKeys is course_add's {'msg': 'validation errors.'} (400).
	ErrCourseWritesBodyKeys = errors.New("course add body keys")
	// ErrCourseWritesServer is a Django server error (500).
	ErrCourseWritesServer = errors.New("course write server error")
	// ErrCourseWritesNotPorted refuses an input whose Django behaviour is not ported (500, before any write).
	ErrCourseWritesNotPorted = errors.New("course write not ported")
	// ErrCourseWritesStalePayment is a course subscription save carrying a
	// payment that was deleted meanwhile: the deferred foreign key fails.
	ErrCourseWritesStalePayment = errors.New("course subscription payment deleted")
)

// CourseWriteRow is one application_course row. Decimals and JSON are the
// stored text.
type CourseWriteRow struct {
	ID               string     `db:"course_id"`
	AssociationID    *string    `db:"sport_association_id"`
	StatusFlag       int        `db:"status_flag"`
	CourseType       int        `db:"course_type"`
	CreationDate     time.Time  `db:"creation_date"`
	Title            *string    `db:"title"`
	Description      *string    `db:"description"`
	MultiPayments    bool       `db:"multi_payments"`
	Fee              *string    `db:"fee"`
	Events           *string    `db:"events"`
	OneFeePayment    bool       `db:"one_fee_payment"`
	OneFee           string     `db:"one_fee"`
	GoogleCalendarID *string    `db:"google_calendar_id"`
	Color            *string    `db:"google_background_color"`
	MultipleQuotes   *string    `db:"multiple_quotes"`
	StartDate        *time.Time `db:"start_date"`
	EndDate          *time.Time `db:"end_date"`
	Pinned           bool       `db:"pinned"`
	SportSeason      bool       `db:"billed_duration_is_sport_season"`
	BilledFrequency  *int       `db:"billed_frequency"`
	FromSubscription bool       `db:"billed_from_subscription_date"`
	BilledDayOfMonth int        `db:"billed_from_day_of_month"`
	AutoRenewal      bool       `db:"auto_renewal"`
	Deleted          bool       `db:"deleted"`
	GroupID          *string    `db:"group_id"`
}

// CourseWriteSubscription is the Subscription the course views read.
type CourseWriteSubscription struct {
	ID            string  `db:"subscription_id"`
	AssociationID *string `db:"sport_association_id"`
	UserID        *string `db:"user_id"`
	AssociateID   *string `db:"associate_id"`
}

// CourseWriteInstallment is a loaded installment instance: its stored
// payment and paid flag, and auditlog's delete diff as loaded.
type CourseWriteInstallment struct {
	ID        string
	PaymentID *string
	Changes   map[string][2]string
}

// CourseWriteTitlePayment is a payment matched by the old course title.
type CourseWriteTitlePayment struct {
	ID          string  `db:"payment_id"`
	Meta        *string `db:"meta"`
	Description *string `db:"description"`
}

// CourseWritePayment is Payment.objects.create(...) followed by save(): the
// stored columns, the in-memory amount text auditlog records (a float
// default renders '0.0') and whether the second save records that amount.
type CourseWritePayment struct {
	ID            string
	Amount        string
	MemoryAmount  string
	CreationDate  time.Time
	Description   string
	CategoryID    *string
	UserID        *string
	AssociateID   *string
	AssociationID *string
	Meta          *string
	AccountID     *string
	GroupID       *string
}

// CourseWriteInstallmentCreate is CourseSubscriptionInstallment.objects.create(...).
type CourseWriteInstallmentCreate struct {
	ID                 string
	CourseSubscription string
	Number             int
	Amount             string
	PaymentDate        time.Time
	PaymentID          string
	GroupID            *string
	// AssociationID is the course's association (the index resolver).
	AssociationID *string
}

// CourseWriteCourseSave is course_update's Course.save(): the assigned row,
// auditlog's changes and the index association.
type CourseWriteCourseSave struct {
	Row     CourseWriteRow
	Changes map[string][2]string
}

// CourseWriteSubscriptionSave is CourseSubscription.save() of an in-memory
// instance; FloatFee marks the float membership_fee default of a row created
// in this request.
type CourseWriteSubscriptionSave struct {
	Row      CourseSubscriptionRow
	FloatFee bool
}

type CourseWritesTransaction interface {
	// OwnerAssociation is SportAssociation.objects.get(user=owner) (ErrAssociationNotFound).
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	// OwnerAssociationFirst is SportAssociation.objects.filter(user=owner).first().
	OwnerAssociationFirst(ctx context.Context, owner string) (*string, error)
	ActorAssociation(ctx context.Context, actor string) (*string, error)
	// LiveCourse is Course.objects.filter(course_id=id).first(): live and group-aware.
	LiveCourse(ctx context.Context, id string, group *string) (*CourseWriteRow, error)
	UserFullInstallmentsPlan(ctx context.Context, user string) (bool, error)
	DefaultPaymentCategory(ctx context.Context) (*string, error)
	DefaultBankAccount(ctx context.Context, association *string) (*string, error)
	GroupExists(ctx context.Context, id string) (bool, error)

	// DeleteCourse is course.delete(): the collector with its signals.
	DeleteCourse(ctx context.Context, course CourseWriteRow, audit CourseSubscriptionAudit) error

	// LinkedLocations are the location keys linked to the course.
	LinkedLocations(ctx context.Context, course string) ([]string, error)
	// LocationLinkable is CourseLocation.objects.filter(Q(sport_association=association) |
	// Q(course_location_id__in=linked), course_location_id=id).exists().
	LocationLinkable(ctx context.Context, id, association string, linked []string) (bool, error)
	ClearLocations(ctx context.Context, course string) error
	AddLocation(ctx context.Context, course, location string) error
	SaveCourse(ctx context.Context, save CourseWriteCourseSave, audit CourseSubscriptionAudit) error
	// TitlePayments are the association's live group-aware payments whose meta
	// text or description contains title case-insensitively.
	TitlePayments(ctx context.Context, title, association string, group *string) ([]CourseWriteTitlePayment, error)
	RewritePayment(ctx context.Context, id string, meta *string, description *string) error

	// CreateCourse inserts the course and its create entry; overrides are
	// auditlog's in-memory renderings.
	CreateCourse(ctx context.Context, row CourseWriteRow, overrides map[string][2]string, audit CourseSubscriptionAudit) error
	// SetCourseAssociation is the second save: the association plus the float defaults.
	SetCourseAssociation(ctx context.Context, course, association string, changes map[string][2]string, audit CourseSubscriptionAudit) error
	LiveSubscription(ctx context.Context, id string, group *string) (*CourseWriteSubscription, error)
	CreateCourseSubscription(ctx context.Context, row CourseSubscriptionRow, audit CourseSubscriptionAudit) error
	CreatePayment(ctx context.Context, payment CourseWritePayment, audit CourseSubscriptionAudit) error
	CreateInstallment(ctx context.Context, installment CourseWriteInstallmentCreate, audit CourseSubscriptionAudit) error

	// LiveCourseSubscription is CourseSubscription.objects.filter(course=..., subscription=...).first().
	LiveCourseSubscription(ctx context.Context, course string, subscription *string) (*CourseSubscriptionRow, error)
	// LiveInstallment is the group-aware live installment id of the row.
	LiveInstallment(ctx context.Context, row, id string, group *string) (*CourseWriteInstallment, error)
	// Installments are the row's live group-aware installments: unpaid
	// (CSI.paid=False) or payable (payment unpaid or null), in key order.
	Installments(ctx context.Context, row string, group *string, payable bool) ([]CourseWriteInstallment, error)
	// PaymentPaid is the payment (base manager): existence and paid flag.
	PaymentPaid(ctx context.Context, id string) (exists, paid bool, err error)
	// DeletePayment is payment.delete() with the pre_delete signals.
	DeletePayment(ctx context.Context, id string, audit CourseSubscriptionAudit) error
	// DeleteInstallment deletes a loaded installment whose own pre_delete
	// does nothing, auditing its loaded diff with overrides.
	DeleteInstallment(ctx context.Context, installment CourseWriteInstallment, overrides map[string][2]string, audit CourseSubscriptionAudit) error
	DeleteInstallmentsOf(ctx context.Context, row string, audit CourseSubscriptionAudit) error
	// SaveCourseSubscription is Model.save(): auditlog's entry, then the
	// UPDATE (ErrCourseWritesStalePayment when the payment no longer exists).
	SaveCourseSubscription(ctx context.Context, save CourseWriteSubscriptionSave, audit CourseSubscriptionAudit) error

	Commit() error
	Rollback() error
}

type CourseWritesStore interface {
	BeginCourseWrites(context.Context) (CourseWritesTransaction, error)
}
