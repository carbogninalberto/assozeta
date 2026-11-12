package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// StripePaidChecks serves mark_as_paid_payments_checking_stripe on the
// paid-flow transaction of the Stripe routes, fenced by the job lease.
type StripePaidChecks struct{ flows *PaymentPaidFlows }

func NewStripePaidChecks(db *sqlx.DB) *StripePaidChecks {
	return &StripePaidChecks{flows: NewPaymentPaidFlows(NewPaymentStates(db))}
}

var _ domain.StripePaidCheckStore = (*StripePaidChecks)(nil)

type stripePaidCheckTransaction struct{ *paymentPaidFlowTransaction }

func (r *StripePaidChecks) BeginStripePaidCheck(ctx context.Context, job domain.Job) (domain.StripePaidCheckTransaction, error) {
	tx, err := r.flows.BeginPaymentPaidFlow(ctx)
	if err != nil {
		return nil, err
	}
	flow := tx.(*paymentPaidFlowTransaction)
	if err = fenceJobEffects(ctx, flow.tx, job.ID, job.LeaseToken); err != nil {
		_ = flow.Rollback()
		return nil, err
	}
	return &stripePaidCheckTransaction{paymentPaidFlowTransaction: flow}, nil
}

// Payment.objects.filter(paid=False, payment_intent_id__isnull=False): live
// payments; Go orders them by key where Django's ten threads finish in any order.
func (t *stripePaidCheckTransaction) StripePaidCheckPayments(ctx context.Context) ([]string, error) {
	ids := []string{}
	err := t.tx.SelectContext(ctx, &ids, `SELECT payment_id::text FROM application_payment
 WHERE NOT deleted AND NOT paid AND payment_intent_id IS NOT NULL ORDER BY payment_id`)
	return ids, err
}

func (t *stripePaidCheckTransaction) LockStripePaidCheck(ctx context.Context, id string) (*domain.StripePaidCheckPayment, error) {
	var row domain.StripePaidCheckPayment
	err := t.tx.GetContext(ctx, &row, `SELECT payment_id::text, paid, type, payment_intent_id, invoice_id::text, user_id::text, sport_association_id::text
 FROM application_payment WHERE NOT deleted AND payment_id=$1::uuid FOR UPDATE`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *stripePaidCheckTransaction) SaveStripePaidCheckType(ctx context.Context, payment domain.StripePaidCheckPayment, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_payment SET type='stripe' WHERE payment_id=$1::uuid`, payment.ID); err != nil {
		return err
	}
	entry := modelAuditEntry{Model: "payment", Key: payment.ID, Representation: "Payment object (" + payment.ID + ")", Action: 1,
		Changes: map[string][2]string{"type": {payment.Type, "stripe"}}, At: at}
	if payment.AssociationID != nil {
		path := "direct"
		entry.AssociationID, entry.ResolutionPath = payment.AssociationID, &path
	}
	return appendModelAudit(ctx, t.tx, entry)
}

func (t *stripePaidCheckTransaction) CompleteJob(ctx context.Context, id, token uuid.UUID) error {
	return completeJob(ctx, t.tx, id, token, nil)
}
