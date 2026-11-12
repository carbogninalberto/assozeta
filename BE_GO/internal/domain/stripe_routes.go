package domain

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

var (
	// ErrStripeServer is an unhandled exception in a stripe view (the 500 page).
	ErrStripeServer = errors.New("stripe view server error")
	// ErrStripeNotPorted refuses, before any write, a path whose Django
	// behaviour is not ported: mark_payment_as_paid for an unpaid payment
	// without an invoice (invoice creation, PDF task and notification).
	ErrStripeNotPorted = refusal.New("stripe view behaviour not ported")
)

// StripePayment is what the stripe views read of one live payment, with its
// association (base manager) and the association owner's online_payments.
type StripePayment struct {
	PaymentSaveRow
	Amount          string     `db:"amount"`
	Paid            bool       `db:"paid"`
	PaymentDate     *time.Time `db:"payment_date"`
	IntentID        *string    `db:"payment_intent_id"`
	InvoiceID       *string    `db:"invoice_id"`
	UserID          *string    `db:"user_id"`
	UserFirst       *string    `db:"user_first_name"`
	UserLast        *string    `db:"user_last_name"`
	AssociateID     *string    `db:"associate_id"`
	AssociateFirst  *string    `db:"associate_first_name"`
	AssociateLast   *string    `db:"associate_last_name"`
	AssociateUserID *string    `db:"associate_user_id"`
	HasAssociation  bool       `db:"has_association"`
	Denomination    *string    `db:"denomination"`
	Methods         *string    `db:"stripe_available_methods"`
	OwnerOnline     *bool      `db:"owner_online_payments"`
}

// StripePaymentQuery is scoped_queryset(request, Payment.objects): live,
// X-Group-Id aware, limited to the session association when impersonating.
type StripePaymentQuery struct {
	Scope *string
	Group *string
}

// StripeIntentMatch is the payment_intent_id lookup value: Null is
// filter(payment_intent_id=None) (IS NULL); NoMatch is a text that equals no
// stored value (a Python repr that embeds an address), so the filter is empty.
type StripeIntentMatch struct {
	Value   string
	Null    bool
	NoMatch bool
}

type StripeRoutesStore interface {
	// PaymentByID is .filter(payment_id=id).first(); nil when none.
	PaymentByID(ctx context.Context, id string, query StripePaymentQuery) (*StripePayment, error)
	// PaymentByIntent is .filter(payment_intent_id=intent).first(); nil when none.
	PaymentByIntent(ctx context.Context, intent string, query StripePaymentQuery) (*StripePayment, error)
	// PaymentsByIntent is .filter(payment_intent_id=...) in database order.
	PaymentsByIntent(ctx context.Context, match StripeIntentMatch, query StripePaymentQuery) ([]StripePayment, error)
	// PaymentIDs is .filter(payment_id__in=ids[, paid=False]) in database order.
	PaymentIDs(ctx context.Context, ids []string, unpaidOnly bool, query StripePaymentQuery) ([]string, error)
	// AssociationCheckout is scoped SportAssociation.objects.filter(pk=id):
	// whether it exists and the first row's checkout_info.
	AssociationCheckout(ctx context.Context, id string, scope *string) (bool, *string, error)
	BeginStripe(ctx context.Context) (StripeRoutesTransaction, error)
}

type StripeRoutesTransaction interface {
	PaymentStateTransaction
	// LockStripePayment is Payment.objects.select_for_update().get(payment_id=id)
	// (X-Group-Id aware); nil when none.
	LockStripePayment(ctx context.Context, id string, group *string) (*StripePayment, error)
	// SavePaymentIntent is Payment.save() after payment_intent_id changed.
	SavePaymentIntent(ctx context.Context, id string, intent, category, description *string) error
}
