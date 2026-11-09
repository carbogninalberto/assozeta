package domain

import (
	"context"
	"errors"
	"time"
)

// ErrPrintingGenerateServer is an exception printing_generate does not catch
// (the 500 page); ErrPrintingGenerateNotPorted is an output Go does not
// reproduce (the in-process reportlab PDF, a workbook input xlsxexport
// refuses), refused with the 500 page before any write.
var (
	ErrPrintingGenerateServer    = errors.New("printing generate: unhandled exception")
	ErrPrintingGenerateNotPorted = errors.New("printing generate: output not ported")
)

// PrintingAssociation is request.user.sport_association.
type PrintingAssociation struct {
	ID           string  `db:"sport_association_id"`
	Denomination *string `db:"denomination"`
}

// PrintingPerson is an associate as the reports read it (all nil for a
// missing associate).
type PrintingPerson struct {
	ID          *string    `db:"associate_id"`
	Association *string    `db:"associate_association"`
	FirstName   *string    `db:"first_name"`
	LastName    *string    `db:"last_name"`
	Email       *string    `db:"email"`
	Phone       *string    `db:"phone"`
	BornDate    *time.Time `db:"born_date"`
}

// PrintingSubscription is one filter_subscriptions row with its
// select_related medical certificate and associate.
type PrintingSubscription struct {
	ID                string     `db:"subscription_id"`
	Type              int64      `db:"type"`
	End               *time.Time `db:"end_date"`
	HasMedical        bool       `db:"has_medical"`
	MedicalExpiration *time.Time `db:"medical_expiration"`
	PrintingPerson
}

// PrintingRelation is a tutor relation of an associate with its tutor.
type PrintingRelation struct {
	ID          int64  `db:"id"`
	AssociateID string `db:"relation_associate_id"`
	Primary     bool   `db:"is_primary"`
	PrintingPerson
}

// PrintingMembership is one expiring SubscriptionMembership with its associate.
type PrintingMembership struct {
	Type *string    `db:"membership_type"`
	End  *time.Time `db:"end_date"`
	PrintingPerson
}

// PrintingPayment is a payment with the related rows the reports read.
type PrintingPayment struct {
	PaymentID     string     `db:"payment_id"`
	Subject       int64      `db:"subject"`
	Amount        string     `db:"amount"`
	Created       time.Time  `db:"creation_date"`
	PaymentDate   *time.Time `db:"payment_date"`
	Paid          bool       `db:"paid"`
	Description   *string    `db:"description"`
	Meta          *string    `db:"meta"`
	Course        *string    `db:"course"`
	HasCategory   bool       `db:"has_category"`
	CategoryName  *string    `db:"category_name"`
	HasAccount    bool       `db:"has_account"`
	HasInvoice    bool       `db:"has_invoice"`
	InvoiceNumber *int64     `db:"invoice_number"`
	PrintingPerson
}

// PrintingSubscriptionFilter is filter_subscriptions' queryset. Statuses nil
// means no status filter; a non-nil empty set matches nothing.
type PrintingSubscriptionFilter struct {
	Association string
	Group       *string
	OnlyMembers bool
	Medical     bool
	// Year is "", "pre-iscrizioni", "corrente" or "precedenti" against Today.
	Year     string
	Today    time.Time
	Statuses []int64
	// StatusFilter is set when the request names statuses (Statuses may then
	// hold no matching value: an out-of-range int).
	StatusFilter bool
}

// PrintingPaymentQuery selects one report's payments.
type PrintingPaymentQuery struct {
	// Kind is "quotes", "courses" or "expired".
	Kind        string
	Association string
	Group       *string
	Associates  []string
	Now         time.Time
	// Period bounds creation_date (>= From, <= To) when set.
	Period   bool
	From, To time.Time
}

// PrintingPaymentLink is get_course_carnet_name's linked row: Slot 0 course
// subscription, 1 installment, 2 carnet subscription; -1 none.
type PrintingPaymentLink struct {
	Slot          int
	Title         *string
	MissingCarnet bool
}

// PrintingPromotion is get_main_tutor's audited save of a relation.
type PrintingPromotion struct {
	RelationID    int64
	Context       LoginAuditContext
	AssociationID *string
	Path          *string
	At            time.Time
}

type PrintingGenerateStore interface {
	BeginPrintingGenerate(ctx context.Context) (PrintingGenerateTransaction, error)
}

type PrintingGenerateTransaction interface {
	Commit() error
	Rollback() error
	// Association is SportAssociation.objects.get(user=...): ErrAssociationNotFound unless exactly one live row.
	Association(ctx context.Context, user string) (*PrintingAssociation, error)
	Subscriptions(ctx context.Context, filter PrintingSubscriptionFilter) ([]PrintingSubscription, error)
	// Relations are the associates' tutor relations by primary key.
	Relations(ctx context.Context, associates []string) ([]PrintingRelation, error)
	Memberships(ctx context.Context, filter PrintingSubscriptionFilter, horizon time.Time) ([]PrintingMembership, error)
	Payments(ctx context.Context, query PrintingPaymentQuery) ([]PrintingPayment, error)
	// AssociatePayments are subscriptions_with_all_payments' payments of one
	// associate (nil: associate IS NULL) by -creation_date.
	AssociatePayments(ctx context.Context, associate *string, association string, group *string, period bool, from, to time.Time) ([]PrintingPayment, error)
	PaymentLink(ctx context.Context, payment string, group *string) (PrintingPaymentLink, error)
	PromoteRelation(ctx context.Context, promotion PrintingPromotion) error
	ActiveAssociationOf(ctx context.Context, user string) (*string, error)
}
