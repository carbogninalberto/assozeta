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
	// SignPayment is scoped_queryset(request, Payment.objects).filter(payment_id=id).first() is not None.
	SignPayment(ctx context.Context, id string, group, scope *string) (bool, error)
}
