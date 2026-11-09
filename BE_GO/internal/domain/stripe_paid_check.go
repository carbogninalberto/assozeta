package domain

import (
	"context"
	"time"

	"github.com/google/uuid"
)

// JobMarkAsPaidPaymentsCheckingStripe is the Celery name of
// mark_as_paid_payments_checking_stripe, as persisted Beat rows name it.
const JobMarkAsPaidPaymentsCheckingStripe = "mark_as_paid_payments_checking_stripe"

// StripePaidCheckPayment is the locked payment _check_and_mark_payment reads
// (Payment.objects.select_for_update().get(payment_id=...)).
type StripePaidCheckPayment struct {
	ID            string  `db:"payment_id"`
	Paid          bool    `db:"paid"`
	Type          string  `db:"type"`
	IntentID      *string `db:"payment_intent_id"`
	InvoiceID     *string `db:"invoice_id"`
	UserID        *string `db:"user_id"`
	AssociationID *string `db:"sport_association_id"`
}

type StripePaidCheckStore interface {
	// BeginStripePaidCheck opens one paid-flow transaction (READ COMMITTED)
	// fenced by the job lease: one _check_and_mark_payment atomic block.
	BeginStripePaidCheck(context.Context, Job) (StripePaidCheckTransaction, error)
}

type StripePaidCheckTransaction interface {
	PaymentPaidFlowTransaction
	// StripePaidCheckPayments is the task's list of unpaid live payments with
	// an intent, in payment_id order (the order Go processes them in).
	StripePaidCheckPayments(context.Context) ([]string, error)
	// LockStripePaidCheck is Payment.objects.select_for_update().get(payment_id=id); nil when absent.
	LockStripePaidCheck(ctx context.Context, id string) (*StripePaidCheckPayment, error)
	// SaveStripePaidCheckType is payment.type = 'stripe'; payment.save(update_fields=['type'])
	// with its update entry (only type), indexed through the payment's association.
	SaveStripePaidCheckType(ctx context.Context, payment StripePaidCheckPayment, at time.Time) error
	CompleteJob(ctx context.Context, id, token uuid.UUID) error
}
