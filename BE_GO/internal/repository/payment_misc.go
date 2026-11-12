package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// PaymentMisc serves the partial quotes simulation and apply and payment sign.
type PaymentMisc struct{ writes *PaymentWrites }

func NewPaymentMisc(states *PaymentStates) *PaymentMisc {
	return &PaymentMisc{writes: NewPaymentWrites(states)}
}

var _ domain.PaymentMiscStore = (*PaymentMisc)(nil)

type paymentMiscTransaction struct{ *paymentWritesTransaction }

func (r *PaymentMisc) BeginPaymentMisc(ctx context.Context) (domain.PaymentMiscTransaction, error) {
	tx, err := r.writes.BeginPaymentWrites(ctx)
	if err != nil {
		return nil, err
	}
	return &paymentMiscTransaction{paymentWritesTransaction: tx.(*paymentWritesTransaction)}, nil
}

func (t *paymentMiscTransaction) SimulationAssociation(ctx context.Context, id string) (*domain.PaymentSimulationAssociation, error) {
	var row domain.PaymentSimulationAssociation
	err := t.tx.GetContext(ctx, &row, `SELECT a.sport_association_id, a.user_id, a.denomination, u.email AS owner_email,
 a.subscription_fee_plans::text AS subscription_fee_plans,
 u.balance_sheet_start_day, u.balance_sheet_start_month
 FROM application_sportassociation a JOIN bakney_user u ON u.user_id=a.user_id WHERE a.sport_association_id=$1`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// SimulationSubscriptions is the balance-year subscription queryset filtered
// by payment__meta__icontains (UPPER(meta::text) LIKE UPPER(pattern)). Like
// Django's queryset it has no ORDER BY.
func (t *paymentMiscTransaction) SimulationSubscriptions(ctx context.Context, query domain.PaymentSimulationQuery) (
	[]domain.PaymentSimulationSubscription, error) {
	rows := []domain.PaymentSimulationSubscription{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT s.subscription_id, s.user_id, u.first_name AS user_first_name, u.last_name AS user_last_name,
 u.email AS user_email, s.associate_id, a.first_name AS associate_first_name, a.last_name AS associate_last_name
 FROM application_subscription s
 JOIN application_payment p ON p.payment_id=s.payment_id
 JOIN bakney_user u ON u.user_id=s.user_id
 LEFT JOIN application_associate a ON a.associate_id=s.associate_id
 WHERE s.sport_association_id=$1 AND s.creation_date >= $2 AND s.creation_date <= $3
 AND p.meta IS NOT NULL AND s.meta IS NOT NULL AND NOT s.deleted AND NOT s.archived
 AND NOT (s.meta = '"{\"plan_id\": null}"'::jsonb)
 AND ($4::uuid IS NULL OR s.group_id=$4::uuid)
 AND UPPER(p.meta::text) LIKE UPPER($5)`, query.Association, query.From, query.To, query.Group, query.Pattern)
	return rows, err
}

func (t *paymentMiscTransaction) QuoteExists(ctx context.Context, check domain.PaymentQuoteCheck) (bool, error) {
	return t.exists(ctx, `SELECT 1 FROM application_payment WHERE user_id=$1 AND subject=1 AND UPPER(meta::text) LIKE UPPER($2)
 AND associate_id IS NOT DISTINCT FROM $3::uuid AND amount IS NOT DISTINCT FROM NULLIF($4, '')::numeric AND NOT deleted AND ($5::uuid IS NULL OR group_id=$5::uuid)`,
		check.User, check.Pattern, check.Associate, check.Amount, check.Group)
}

func (t *paymentMiscTransaction) SignPayment(ctx context.Context, id string, group, scope *string) (bool, *string, error) {
	var association *string
	err := t.tx.GetContext(ctx, &association, `SELECT sport_association_id::text FROM application_payment WHERE payment_id=$1 AND NOT deleted
 AND ($2::uuid IS NULL OR group_id=$2::uuid) AND ($3::uuid IS NULL OR sport_association_id=$3::uuid) LIMIT 1`, id, group, scope)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil, nil
	}
	return err == nil, association, err
}

func (t *paymentMiscTransaction) CommunicationConfigured(ctx context.Context, association string) (bool, error) {
	return t.exists(ctx, `SELECT 1 FROM communications_communicationconfiguration WHERE sport_association_id=$1`, association)
}

// CreatePaymentSignature inserts the Signature and its create entry; the
// resolver finds no subscription, so the index falls back to the actor's
// own association (none: no index row).
func (t *paymentMiscTransaction) CreatePaymentSignature(ctx context.Context, save domain.PaymentSignatureSave) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_signature (signature_id, user_id, signature) VALUES ($1, $2, $3)`,
		save.ID, save.User, save.Data); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_signature", "signature_id", save.ID)
	if err != nil {
		return err
	}
	var path *string
	if save.AssociationID != nil {
		value := "actor.sport_association"
		path = &value
	}
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "signature", Key: save.ID, Representation: "Signature object (" + save.ID + ")",
		Action: 0, Changes: changes, Context: save.Audit, AssociationID: save.AssociationID, ResolutionPath: path, At: save.At})
}

func (t *paymentMiscTransaction) SetPaymentSignature(ctx context.Context, payment, signature string) (*string, error) {
	var previous *string
	err := t.tx.GetContext(ctx, &previous, `UPDATE application_payment p SET signature_id=$2
 FROM (SELECT signature_id FROM application_payment WHERE payment_id=$1) o WHERE p.payment_id=$1 RETURNING o.signature_id::text`, payment, signature)
	return previous, err
}
