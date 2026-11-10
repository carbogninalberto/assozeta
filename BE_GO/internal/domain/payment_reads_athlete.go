package domain

import (
	"context"
	"time"
)

// PaymentAthleteScope is what the athlete list's querysets depend on: the
// athlete, the X-Group-Id of the group-aware managers and, in an
// association-scoped session, scoped_queryset's association (nil outside one).
type PaymentAthleteScope struct {
	Association *string
	Person      string
	Group       *string
}

// PaymentAthleteRow is one listed payment with what PaymentCompressedSerializer
// and the view's flags read from its relations.
type PaymentAthleteRow struct {
	ID            string     `db:"payment_id"`
	Type          string     `db:"type"`
	Subject       int        `db:"subject"`
	Amount        string     `db:"amount"`
	Description   *string    `db:"description"`
	Paid          bool       `db:"paid"`
	CreationDate  time.Time  `db:"creation_date"`
	PaymentDate   *time.Time `db:"payment_date"`
	CategoryID    *string    `db:"payment_category_id"`
	AssociationID *string    `db:"sport_association_id"`
	Notes         *string    `db:"notes"`
	Course        *string    `db:"course"`
	AssociateID   *string    `db:"associate_id"`
	AssociateName *string    `db:"associate_first_name"`
	AssociateLast *string    `db:"associate_last_name"`
	InvoiceID     *string    `db:"invoice_id"`
	// InvoiceDocument is the invoice's document_pdf.
	InvoiceDocument *string `db:"invoice_document"`
	Denomination    *string `db:"denomination"`
	// OwnerOnline is the association owner's online_payments (nil without an owner).
	OwnerOnline *bool `db:"owner_online"`
}

// PaymentAthleteCourseSubscription is the course subscription of the
// course_subscription_id branch, with its payment and the course owner's
// show_zero_payments (nil when the course has no association).
type PaymentAthleteCourseSubscription struct {
	ID              string  `db:"course_subscription_id"`
	MultiPayments   bool    `db:"multi_payments"`
	PaymentID       *string `db:"payment_id"`
	PaymentPositive *bool   `db:"payment_positive"`
	ShowZero        *bool   `db:"show_zero"`
}

// PaymentAthleteCandidate is one installment or carnet holding of that course
// subscription: its payment, whether the compared amount is above zero and,
// for a holding, the carnet owner's show_zero_payments.
type PaymentAthleteCandidate struct {
	PaymentID *string `db:"payment_id"`
	Positive  *bool   `db:"positive"`
	ShowZero  *bool   `db:"show_zero"`
}

// PaymentAthleteExclusion is one live course subscription of the athlete's
// subscriptions, joined to each unpaid live installment when its course has
// multi_payments.
type PaymentAthleteExclusion struct {
	Multi              bool    `db:"multi"`
	CoursePayment      *string `db:"course_payment"`
	CourseZero         *bool   `db:"course_zero"`
	HasInstallment     bool    `db:"has_installment"`
	InstallmentPayment *string `db:"installment_payment"`
	InstallmentZero    *bool   `db:"installment_zero"`
	Late               *bool   `db:"late"`
	ShowZero           bool    `db:"show_zero"`
}

// PaymentAthleteInstallment is an installment of a listed payment with its
// course's one-fee settings.
type PaymentAthleteInstallment struct {
	PaymentID   string    `db:"payment_id"`
	PaymentDate time.Time `db:"payment_date"`
	OneFee      bool      `db:"one_fee_payment"`
	PaymentPaid *bool     `db:"payment_paid"`
	Events      *string   `db:"events"`
}

// Kinds of PaymentAthleteMeta, in get_meta's lookup order.
const (
	PaymentMetaNone         = ""
	PaymentMetaInstallment  = "installment"
	PaymentMetaSubscription = "subscription"
	PaymentMetaCarnet       = "carnet"
	PaymentMetaCourse       = "course"
	PaymentMetaCamp         = "camp"
)

// PaymentAthleteMeta is what get_meta found for a payment.
type PaymentAthleteMeta struct {
	Kind string
	// Number and Date are the installment's id and payment_date.
	Number         *int64
	Date           *time.Time
	Name           *string
	SubscriptionID string
	Start, End     *string
}

type PaymentAthleteStore interface {
	// CourseSubscription is the scoped live course subscription (nil when missing).
	CourseSubscription(ctx context.Context, id string, scope PaymentAthleteScope) (*PaymentAthleteCourseSubscription, error)
	// InstallmentCandidates are its scoped live installments (Positive: the installment's amount).
	InstallmentCandidates(ctx context.Context, courseSubscription string, scope PaymentAthleteScope) ([]PaymentAthleteCandidate, error)
	// CarnetCandidates are the scoped carnet holdings linked to it (Positive: the payment's amount).
	CarnetCandidates(ctx context.Context, courseSubscription string, scope PaymentAthleteScope) ([]PaymentAthleteCandidate, error)
	// ByIDs are the scoped live payments with those ids, in database order.
	ByIDs(ctx context.Context, ids []string, scope PaymentAthleteScope) ([]PaymentAthleteRow, error)
	// Listed are the athlete's scoped live income payments created up to limit, by paid, -creation_date.
	Listed(ctx context.Context, scope PaymentAthleteScope, limit time.Time) ([]PaymentAthleteRow, error)
	// Exclusions are the rows the exclusion rules read; today is the date installments are compared with.
	Exclusions(ctx context.Context, scope PaymentAthleteScope, today time.Time) ([]PaymentAthleteExclusion, error)
	// Installments are the scoped live installments of the payments, in database order.
	Installments(ctx context.Context, payments []string, scope PaymentAthleteScope) ([]PaymentAthleteInstallment, error)
	// Meta is get_meta's lookups through the group-aware managers; ErrPaymentReadsServer where Django raises.
	Meta(ctx context.Context, payment string, group *string) (PaymentAthleteMeta, error)
}
