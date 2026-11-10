package domain

import (
	"context"
	"errors"
	"time"
)

var (
	// ErrPaymentMiscServer is an unhandled exception in the Django view (the 500 page).
	ErrPaymentMiscServer = errors.New("payment misc server error")
	// ErrPaymentMiscNotPorted refuses, before any write, an input whose Django
	// behaviour is not ported (e-mails, signature storage with its PDF, plan
	// values outside the ported Python conversions).
	ErrPaymentMiscNotPorted = errors.New("payment misc behaviour not ported")
	// ErrPaymentMiscBadRequest is payment_sign's 400 {'msg': 'validation errors.'}.
	ErrPaymentMiscBadRequest = errors.New("validation errors.")
)

// PaymentMiscValidation is a 400 of DRF field error codes by field name.
type PaymentMiscValidation struct{ Fields map[string][]string }

func (e *PaymentMiscValidation) Error() string { return "payment misc validation" }

// PaymentSimulationAssociation is the association calculate_simulation reads:
// its stored plans and its owner's balance year start.
type PaymentSimulationAssociation struct {
	ID           string  `db:"sport_association_id"`
	OwnerID      string  `db:"user_id"`
	Denomination *string `db:"denomination"`
	OwnerEmail   *string `db:"owner_email"`
	Plans        *string `db:"subscription_fee_plans"`
	StartDay     int     `db:"balance_sheet_start_day"`
	StartMonth   int     `db:"balance_sheet_start_month"`
}

// PaymentSimulationSubscription is one balance-year subscription with its user and associate.
type PaymentSimulationSubscription struct {
	ID             string  `db:"subscription_id"`
	UserID         string  `db:"user_id"`
	UserFirst      *string `db:"user_first_name"`
	UserLast       *string `db:"user_last_name"`
	UserEmail      *string `db:"user_email"`
	AssociateID    *string `db:"associate_id"`
	AssociateFirst *string `db:"associate_first_name"`
	AssociateLast  *string `db:"associate_last_name"`
}

// PaymentSimulationQuery selects the subscriptions whose payment meta icontains Pattern.
type PaymentSimulationQuery struct {
	Association string
	From, To    time.Time
	Pattern     string
	Group       *string
}

// PaymentQuoteCheck is the live group-aware payment lookup that skips an existing quote.
type PaymentQuoteCheck struct {
	User      string
	Associate *string
	Pattern   string
	Amount    string
	Group     *string
}

type PaymentMiscStore interface {
	BeginPaymentMisc(context.Context) (PaymentMiscTransaction, error)
}

type PaymentMiscTransaction interface {
	PaymentWritesTransaction
	SimulationAssociation(ctx context.Context, id string) (*PaymentSimulationAssociation, error)
	SimulationSubscriptions(ctx context.Context, query PaymentSimulationQuery) ([]PaymentSimulationSubscription, error)
	QuoteExists(ctx context.Context, check PaymentQuoteCheck) (bool, error)
	// SignPayment is scoped_queryset(request, Payment.objects).filter(payment_id=id).first(): whether it
	// exists and its sport_association_id.
	SignPayment(ctx context.Context, id string, group, scope *string) (bool, *string, error)
	// CommunicationConfigured is CommunicationConfiguration.objects.filter(sport_association_id=...).first() is not None.
	CommunicationConfigured(ctx context.Context, association string) (bool, error)
	// CreatePaymentSignature is Signature.objects.create(signature=..., user=...) with its audited create.
	CreatePaymentSignature(ctx context.Context, save PaymentSignatureSave) error
	// SetPaymentSignature writes the payment's signature_id (the rest of Payment.save() goes through
	// SavePaymentState) and returns the previous one.
	SetPaymentSignature(ctx context.Context, payment, signature string) (*string, error)
}

// PaymentSignatureSave is payment_sign's Signature row and its audit entry.
type PaymentSignatureSave struct {
	ID, User, Data string
	Audit          LoginAuditContext
	// AssociationID indexes the create entry (the actor's own association); nil leaves it unindexed.
	AssociationID *string
	At            time.Time
}
