package domain

import (
	"context"
	"errors"
	"time"
)

var (
	// ErrCourseSubscriptionNotFound is get_object_or_404's
	// {'detail': 'No CourseSubscription matches the given query.'}.
	ErrCourseSubscriptionNotFound = errors.New("course subscription not found")
	// ErrCourseSubscriptionServer is an unhandled exception in the view
	// (Django's 500 page).
	ErrCourseSubscriptionServer = errors.New("course subscription server error")
)

// CourseSubscriptionRow is one application_coursesubscription row with its
// course. MultipleQuote is the stored JSON text, MembershipFee the decimal text.
type CourseSubscriptionRow struct {
	ID                   string     `db:"course_subscription_id"`
	CourseID             string     `db:"course_id"`
	Paid                 bool       `db:"paid"`
	CreationDate         time.Time  `db:"creation_date"`
	SubscriptionID       *string    `db:"subscription_id"`
	PaymentID            *string    `db:"payment_id"`
	MultiPayments        bool       `db:"multi_payments"`
	OneFeePayment        bool       `db:"one_fee_payment"`
	Archived             bool       `db:"archived"`
	Type                 int        `db:"type"`
	MultipleQuote        *string    `db:"multiple_quote"`
	BilledFrequency      int        `db:"billed_frequency"`
	BilledFrom           *time.Time `db:"billed_from"`
	BilledUntil          *time.Time `db:"billed_until"`
	BilledFromDayOfMonth int        `db:"billed_from_day_of_month"`
	MembershipActive     bool       `db:"membership_active"`
	MembershipFee        *string    `db:"membership_fee"`
	AutoRenewal          bool       `db:"auto_renewal"`
	Deleted              bool       `db:"deleted"`
	CourseAssociationID  *string    `db:"course_association_id"`
	CourseTitle          *string    `db:"course_title"`
	CourseType           int        `db:"course_type"`
}

// CourseSubscriptionQuery is get_queryset(): the association's rows through
// the soft-delete manager (or all_objects()), the optional course and
// subscription filters, and get_object()'s pk. Scope is scoped_queryset()'s
// impersonation association.
type CourseSubscriptionQuery struct {
	AssociationID  string
	CourseID       *string
	SubscriptionID *string
	IncludeDeleted bool
	ID             *string
	// IDs restricts the rows to these keys when not nil (bulk_delete).
	IDs   []string
	Scope *string
	Lock  bool
}

// CourseSubscriptionPerson is SubscriptionSerializerSimplify's subscription
// with its associate (AssociateSerializer) and the medical-label inputs.
type CourseSubscriptionPerson struct {
	ID                 string     `db:"subscription_id"`
	StartDate          *time.Time `db:"start_date"`
	EndDate            *time.Time `db:"end_date"`
	CreationDate       time.Time  `db:"creation_date"`
	MedicalID          *string    `db:"medical_id"`
	MedicalExpiration  *time.Time `db:"expiration_date"`
	MedicalCompetitive *bool      `db:"competitive_medical_certificate"`
	Associate          *InconsistencyAssociate
	AssociateID        *string `db:"associate_id"`
}

// CourseSubscriptionInstallmentItem is CourseEventsSerializer's row.
type CourseSubscriptionInstallmentItem struct {
	ID                 string    `db:"course_subscription_installment_id"`
	CourseSubscription string    `db:"course_subscription_id"`
	Number             int       `db:"id"`
	Amount             string    `db:"amount"`
	PaymentDate        time.Time `db:"payment_date"`
	Paid               bool      `db:"paid"`
}

// CourseSubscriptionCarnet is one enabled carnet holding of a course
// subscription (get_carnets).
type CourseSubscriptionCarnet struct {
	CourseSubscription string    `db:"coursesubscription_id"`
	ID                 string    `db:"carnet_subscription_id"`
	CarnetID           *string   `db:"carnet_id"`
	Title              *string   `db:"title"`
	Lessons            *int      `db:"lessons_number"`
	Meta               *string   `db:"meta"`
	CreationDate       time.Time `db:"creation_date"`
}

// CourseSubscriptionPayment is a membership payment rendered by
// PaymentSerializer, with its associate.
type CourseSubscriptionPayment struct {
	InvoicePartyPayment
	AssociateRow *InconsistencyAssociate
	// Instructor and Invoice are PaymentSerializer's nested rows, loaded for
	// the membership payments the course subscription list renders.
	Instructor *InstructorRow
	Invoice    *PaymentReadsInvoice
}

// CourseSubscriptionCourse is the course field's queryset row (live courses).
type CourseSubscriptionCourse struct {
	ID            string  `db:"course_id"`
	AssociationID *string `db:"sport_association_id"`
	Type          int     `db:"course_type"`
	Title         *string `db:"title"`
}

// CourseSubscriptionMembershipPayment is a payment of the membership
// matching in update().
type CourseSubscriptionMembershipPayment struct {
	ID   string  `db:"payment_id"`
	Paid bool    `db:"paid"`
	Meta *string `db:"meta"`
}

// CourseSubscriptionAudit carries the request's audit context and the actor's
// own association (the index fallback for rows that no longer resolve).
type CourseSubscriptionAudit struct {
	Context          LoginAuditContext
	ActorAssociation *string
	At               time.Time
	// Group is the X-Group-Id filter of the group-aware managers.
	Group *string
}

// CourseSubscriptionPaymentState is the stored payment the update saves.
// CourseSubscriptionEventInstallment is an installment the events edit
// loads (live, group-aware, in the database's order).
type CourseSubscriptionEventInstallment struct {
	ID          string    `db:"course_subscription_installment_id"`
	Number      int       `db:"id"`
	PaymentID   *string   `db:"payment_id"`
	PaymentDate time.Time `db:"payment_date"`
}

// CourseSubscriptionEventContext is what Payment.objects.create needs for
// an event payment of the course's association: its owner user (nil when the
// association is gone), the default bank account and the default category.
type CourseSubscriptionEventContext struct {
	UserID     *string
	AccountID  *string
	CategoryID *string
}

type CourseSubscriptionPaymentState struct {
	Amount        string     `db:"amount"`
	CreationDate  time.Time  `db:"creation_date"`
	PaymentDate   *time.Time `db:"payment_date"`
	Description   *string    `db:"description"`
	Meta          *string    `db:"meta"`
	CategoryID    *string    `db:"payment_category_id"`
	AssociationID *string    `db:"sport_association_id"`
}

// CourseSubscriptionSave is Model.save() of the instance: the stored row,
// auditlog's changes (none: no entry), the in-memory repr and the index.
// Unseen marks a row the default manager hides (auditlog then diffs every
// field against None).
type CourseSubscriptionSave struct {
	Row            CourseSubscriptionRow
	Unseen         bool
	Changes        map[string][2]string
	Representation string
	AssociationID  *string
	ResolutionPath string
}

// CourseSubscriptionPaymentSave is Payment.save() of the matched membership
// payment (the columns the update changes).
type CourseSubscriptionPaymentSave struct {
	ID            string
	Amount        string
	CreationDate  *time.Time
	PaymentDate   *time.Time
	Description   string
	Meta          string
	CategoryID    *string
	Changes       map[string][2]string
	AssociationID *string
}

type CourseSubscriptionTransaction interface {
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	ActorAssociation(ctx context.Context, actor string) (*string, error)
	CourseSubscriptions(ctx context.Context, query CourseSubscriptionQuery) ([]CourseSubscriptionRow, error)
	// Render loads the serializer's nested data for rows.
	People(ctx context.Context, ids []string) (map[string]CourseSubscriptionPerson, error)
	Installments(ctx context.Context, rows []string, group *string) ([]CourseSubscriptionInstallmentItem, error)
	MembershipPayments(ctx context.Context, rows []string, group *string) (map[string][]CourseSubscriptionPayment, error)
	Carnets(ctx context.Context, rows []string) ([]CourseSubscriptionCarnet, error)

	Course(ctx context.Context, id string) (*CourseSubscriptionCourse, error)
	SubscriptionVisible(ctx context.Context, id, association string, group *string) (bool, error)
	CourseSubscriptionIDTaken(ctx context.Context, id, exclude string) (bool, error)
	// MembershipPaymentsWhere returns up to limit membership payments of the
	// row (PaymentManager: live, group-aware) with the optional unpaid and
	// meta billed_until filters, locked.
	MembershipPaymentsWhere(ctx context.Context, row string, group *string, unpaid bool, billedUntil *string, limit int) ([]CourseSubscriptionMembershipPayment, error)
	MembershipPaymentOverlaps(ctx context.Context, row, exclude string, group *string, end, start string) (bool, error)
	DefaultPaymentCategory(ctx context.Context) (*string, error)
	PaymentState(ctx context.Context, payment string) (CourseSubscriptionPaymentState, error)
	// SharedPayment reports whether the row's own payment is also the unpaid
	// payment of one of its live group-aware installments.
	SharedPayment(ctx context.Context, row CourseSubscriptionRow, group *string) (bool, error)
	SaveCourseSubscription(ctx context.Context, save CourseSubscriptionSave, audit CourseSubscriptionAudit) error
	SavePayment(ctx context.Context, save CourseSubscriptionPaymentSave, audit CourseSubscriptionAudit) error

	// The serializer's events edit (CourseSubscriptionOverviewSerializer.update).
	EventInstallments(ctx context.Context, row string, group *string) ([]CourseSubscriptionEventInstallment, error)
	PaymentPaid(ctx context.Context, payment string) (bool, error)
	// DeleteInstallment is installment.delete() with its pre_delete signal.
	DeleteInstallment(ctx context.Context, id string, audit CourseSubscriptionAudit) error
	EventPaymentContext(ctx context.Context, association string) (CourseSubscriptionEventContext, error)
	SubscriptionAssociate(ctx context.Context, subscription string) (bool, *string, error)
	GroupExists(ctx context.Context, id string) (bool, error)
	CreateEventPayment(ctx context.Context, payment CourseWritePayment, audit CourseSubscriptionAudit) error
	CreateEventInstallment(ctx context.Context, installment CourseWriteInstallmentCreate, audit CourseSubscriptionAudit) error

	// SoftDelete is CourseSubscription.delete(): the live group-aware
	// installments through the collector and their signals.
	DeleteInstallmentsOf(ctx context.Context, row string, audit CourseSubscriptionAudit) error
	// BulkDelete is QuerySet.delete() of the rows with the pre_delete signals.
	BulkDelete(ctx context.Context, rows []CourseSubscriptionRow, audit CourseSubscriptionAudit) error
	Commit() error
	Rollback() error
}

type CourseSubscriptionStore interface {
	BeginCourseSubscription(context.Context) (CourseSubscriptionTransaction, error)
}
