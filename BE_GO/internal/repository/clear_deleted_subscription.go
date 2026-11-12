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

type ClearDeletedSubscriptions struct {
	db  *sqlx.DB
	now func() time.Time
}

func NewClearDeletedSubscriptions(db *sqlx.DB, now func() time.Time) *ClearDeletedSubscriptions {
	return &ClearDeletedSubscriptions{db: db, now: now}
}

func (r *ClearDeletedSubscriptions) BeginClearDeleted(ctx context.Context, job domain.Job) (domain.ClearDeletedSubscriptionTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	if err = fenceJobEffects(ctx, tx.tx, job.ID, job.LeaseToken); err != nil {
		_ = tx.Rollback()
		return nil, err
	}
	// The task has no request: no actor, correlation id or X-Group-Id.
	run := &collaboratorDeleteRun{tx: tx.tx, graph: collaboratorDeleteGraphLoaded, now: r.now}
	return &clearDeletedTransaction{ownedTransaction: tx, run: run}, nil
}

type clearDeletedTransaction struct {
	*ownedTransaction
	run *collaboratorDeleteRun
}

func (t *clearDeletedTransaction) DeletedSubscriptionIDs(ctx context.Context) ([]uuid.UUID, error) {
	ids := []uuid.UUID{}
	err := t.tx.SelectContext(ctx, &ids, `SELECT subscription_id FROM application_subscription WHERE deleted ORDER BY subscription_id`)
	return ids, err
}

// PurgeDeletedSubscription is the atomic block of clear_deleted_subscription
// for one key: select_for_update().filter(pk=..., deleted=True).first(), then
// _purge_deleted_subscription through the Collector port.
func (t *clearDeletedTransaction) PurgeDeletedSubscription(ctx context.Context, id uuid.UUID) (bool, *string, error) {
	found, key, err := t.purge(ctx, id)
	if errors.Is(err, ErrCollaboratorDeleteUnsupported) {
		err = errors.Join(domain.ErrClearDeletedUnsupported, err)
	}
	return found, key, err
}

func (t *clearDeletedTransaction) purge(ctx context.Context, id uuid.UUID) (bool, *string, error) {
	var locked []struct {
		Payment *string `db:"payment"`
		Key     *string `db:"key"`
	}
	if err := t.tx.SelectContext(ctx, &locked, `SELECT payment_id::text AS payment,signature_storage_key AS key FROM application_subscription
 WHERE subscription_id=$1 AND deleted ORDER BY subscription_id LIMIT 1 FOR UPDATE`, id); err != nil {
		return false, nil, err
	}
	if len(locked) == 0 {
		return false, nil, nil
	}
	// The payments of the subscription, its course subscriptions and their
	// installments (base managers: deleted rows included).
	payments := []string{}
	if err := t.tx.SelectContext(ctx, &payments, `SELECT DISTINCT payment_id::text FROM (
 SELECT $2::uuid AS payment_id
 UNION ALL SELECT cs.payment_id FROM application_coursesubscription cs WHERE cs.subscription_id=$1
 UNION ALL SELECT i.payment_id FROM application_coursesubscriptioninstallment i
  WHERE i.course_subscription_id IN (SELECT course_subscription_id FROM application_coursesubscription WHERE subscription_id=$1)
) p WHERE payment_id IS NOT NULL`, id, locked[0].Payment); err != nil {
		return false, nil, err
	}
	if err := t.removeUnpaidPayments(ctx, payments); err != nil {
		return false, nil, err
	}
	// Detach the remaining (paid) payments: two plain UPDATEs, no signals.
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_coursesubscriptioninstallment SET payment_id=NULL
 WHERE course_subscription_id IN (SELECT course_subscription_id FROM application_coursesubscription WHERE subscription_id=$1)`, id); err != nil {
		return false, nil, err
	}
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_coursesubscription SET payment_id=NULL WHERE subscription_id=$1`, id); err != nil {
		return false, nil, err
	}
	model := t.run.graph.Models["application.Subscription"]
	rows, err := t.run.load(ctx, model, `t.subscription_id = $1::uuid`, id)
	if err != nil {
		return false, nil, err
	}
	if err = t.run.Delete(ctx, "application.Subscription", rows); err != nil {
		return false, nil, err
	}
	return true, locked[0].Key, nil
}

// removeUnpaidPayments is _remove_unpaid_payments: the unpaid payments among
// ids are deleted, then the invoices only they used.
func (t *clearDeletedTransaction) removeUnpaidPayments(ctx context.Context, ids []string) error {
	if len(ids) == 0 {
		return nil
	}
	var unpaid []struct {
		ID      string  `db:"id"`
		Invoice *string `db:"invoice"`
	}
	if err := t.tx.SelectContext(ctx, &unpaid, `SELECT payment_id::text AS id,invoice_id::text AS invoice FROM application_payment
 WHERE payment_id = ANY($1::text[]::uuid[]) AND NOT paid`, ids); err != nil {
		return err
	}
	unpaidIDs, invoices := []string{}, []string{}
	seen := map[string]bool{}
	for _, row := range unpaid {
		unpaidIDs = append(unpaidIDs, row.ID)
		if row.Invoice != nil && !seen[*row.Invoice] {
			seen[*row.Invoice] = true
			invoices = append(invoices, *row.Invoice)
		}
	}
	removable := []string{}
	if err := t.tx.SelectContext(ctx, &removable, `SELECT i::text FROM unnest($1::text[]::uuid[]) AS i
 WHERE NOT EXISTS (SELECT 1 FROM application_payment p WHERE p.invoice_id=i AND NOT (p.payment_id = ANY($2::text[]::uuid[])))`,
		invoices, unpaidIDs); err != nil {
		return err
	}
	payments := t.run.graph.Models["application.Payment"]
	rows, err := t.run.loadBy(ctx, payments, payments.PK, payments.PKType, unpaidIDs)
	if err != nil {
		return err
	}
	if err = t.run.Delete(ctx, "application.Payment", rows); err != nil {
		return err
	}
	invoiceModel := t.run.graph.Models["application.Invoice"]
	rows, err = t.run.loadBy(ctx, invoiceModel, invoiceModel.PK, invoiceModel.PKType, removable)
	if err != nil {
		return err
	}
	return t.run.Delete(ctx, "application.Invoice", rows)
}

func (t *clearDeletedTransaction) SignatureKeyReferenced(ctx context.Context, key string) (bool, error) {
	var referenced bool
	err := t.tx.GetContext(ctx, &referenced, `SELECT EXISTS(SELECT 1 FROM application_subscription WHERE signature_storage_key=$1)`, key)
	return referenced, err
}

func (t *clearDeletedTransaction) CompleteJob(ctx context.Context, id, token uuid.UUID) error {
	return completeJob(ctx, t.tx, id, token, nil)
}
func (t *clearDeletedTransaction) Commit() error   { return t.ownedTransaction.Commit() }
func (t *clearDeletedTransaction) Rollback() error { return t.ownedTransaction.Rollback() }
