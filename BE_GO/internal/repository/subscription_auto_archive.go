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

type SubscriptionAutoArchives struct {
	db  *sqlx.DB
	now func() time.Time
}

func NewSubscriptionAutoArchives(db *sqlx.DB, now func() time.Time) *SubscriptionAutoArchives {
	return &SubscriptionAutoArchives{db: db, now: now}
}

func (r *SubscriptionAutoArchives) BeginSubscriptionArchive(ctx context.Context, job domain.Job) (domain.SubscriptionArchiveTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	if err = fenceJobEffects(ctx, tx.tx, job.ID, job.LeaseToken); err != nil {
		_ = tx.Rollback()
		return nil, err
	}
	return &subscriptionArchiveTransaction{ownedTransaction: tx, now: r.now}, nil
}

type subscriptionArchiveTransaction struct {
	*ownedTransaction
	now func() time.Time
}

// Subscription._base_manager.filter(archived=False, deleted=False,
// sport_association__user__auto_archive=True).select_related(
// 'sport_association__user', 'payment__invoice'): inner joins without the
// association or user managers' filters, a left join to any payment. Django
// leaves the order to the plan; Go processes the rows by key.
const subscriptionArchiveCandidatesSQL = `SELECT s.subscription_id AS id,s.payment_id AS paymentid,p.invoice_id AS invoiceid,
 s.creation_date AS created,s.start_date AS startdate,s.end_date AS enddate,
 u.balance_sheet_start_day AS fiscalday,u.balance_sheet_start_month AS fiscalmonth,
 u.subscription_start_day AS startday,u.subscription_start_month AS startmonth,
 u.subscription_end_day AS endday,u.subscription_end_month AS endmonth,u.custom_end_date AS customend,u.subscription_duration AS duration
 FROM application_subscription s
 JOIN application_sportassociation a ON a.sport_association_id=s.sport_association_id
 JOIN bakney_user u ON u.user_id=a.user_id
 LEFT JOIN application_payment p ON p.payment_id=s.payment_id
 WHERE NOT s.archived AND NOT s.deleted AND u.auto_archive
 ORDER BY s.subscription_id`

func (t *subscriptionArchiveTransaction) ArchiveCandidates(ctx context.Context) ([]domain.SubscriptionArchiveCandidate, error) {
	var rows []struct {
		ID                                                                       uuid.UUID
		PaymentID, InvoiceID                                                     uuid.NullUUID
		Created                                                                  time.Time
		StartDate, EndDate                                                       sql.NullTime
		FiscalDay, FiscalMonth, StartDay, StartMonth, EndDay, EndMonth, Duration int
		CustomEnd                                                                bool
	}
	if err := t.tx.SelectContext(ctx, &rows, subscriptionArchiveCandidatesSQL); err != nil {
		return nil, err
	}
	date := func(d sql.NullTime) *domain.CalendarDate {
		if !d.Valid {
			return nil
		}
		y, m, day := d.Time.Date()
		return &domain.CalendarDate{Year: y, Month: int(m), Day: day}
	}
	key := func(id uuid.NullUUID) *uuid.UUID {
		if !id.Valid {
			return nil
		}
		value := id.UUID
		return &value
	}
	result := make([]domain.SubscriptionArchiveCandidate, len(rows))
	for i, row := range rows {
		result[i] = domain.SubscriptionArchiveCandidate{ID: row.ID, PaymentID: key(row.PaymentID), InvoiceID: key(row.InvoiceID),
			Created: row.Created.UTC(),
			Fiscal:  domain.CalendarRule{StartDay: row.FiscalDay, StartMonth: row.FiscalMonth},
			Rule: domain.SubscriptionDateRule{Duration: row.Duration, Season: domain.CalendarRule{StartDay: row.StartDay,
				StartMonth: row.StartMonth, CustomEnd: row.CustomEnd, EndDay: row.EndDay, EndMonth: row.EndMonth}},
			Dates: domain.SubscriptionDates{Start: date(row.StartDate), End: date(row.EndDate)}}
	}
	return result, nil
}

// archiveModel is one model _archive_subscription saves with
// update_fields=['archived'].
type archiveModel struct {
	table, key, model, label string
	// softDelete: the default manager (auditlog's old-row lookup and the
	// audit index resolver) excludes deleted rows.
	softDelete bool
}

var (
	archiveSubscriptionModel = archiveModel{"application_subscription", "subscription_id", "subscription", "Subscription", false}
	archivePaymentModel      = archiveModel{"application_payment", "payment_id", "payment", "Payment", true}
	archiveInvoiceModel      = archiveModel{"application_invoice", "invoice_id", "invoice", "Invoice", true}
)

var errArchiveNoRows = errors.New("Save with update_fields did not affect any rows.")

func (t *subscriptionArchiveTransaction) ArchiveSubscription(ctx context.Context, c domain.SubscriptionArchiveCandidate) error {
	if err := t.archiveRow(ctx, archiveSubscriptionModel, c.ID); err != nil {
		return err
	}
	if c.PaymentID == nil {
		return nil
	}
	if err := t.archiveRow(ctx, archivePaymentModel, *c.PaymentID); err != nil {
		return err
	}
	if c.InvoiceID == nil {
		return nil
	}
	return t.archiveRow(ctx, archiveInvoiceModel, *c.InvoiceID)
}

// archiveRow is Model.save(update_fields=['archived']): auditlog's pre_save
// diff of archived against the default manager's row (None when that manager
// does not find it), indexed 'direct' when the resolver's default-manager
// lookup finds the row with an association; then the UPDATE through the
// base manager, which must match a row.
func (t *subscriptionArchiveTransaction) archiveRow(ctx context.Context, m archiveModel, id uuid.UUID) error {
	var stored []struct {
		Archived, Deleted bool
		Association       *string
	}
	if err := t.tx.SelectContext(ctx, &stored, `SELECT archived,deleted,sport_association_id::text AS association FROM `+m.table+
		` WHERE `+m.key+`=$1`, id); err != nil {
		return err
	}
	visible := len(stored) == 1 && !(m.softDelete && stored[0].Deleted)
	var changes map[string][2]string
	switch {
	case !visible:
		changes = map[string][2]string{"archived": {"None", "True"}}
	case !stored[0].Archived:
		changes = map[string][2]string{"archived": {"False", "True"}}
	}
	if changes != nil {
		entry := modelAuditEntry{Model: m.model, Key: id.String(), Representation: m.label + " object (" + id.String() + ")",
			Action: 1, Changes: changes, At: t.now().UTC().Truncate(time.Microsecond)}
		if visible && stored[0].Association != nil {
			path := "direct"
			entry.AssociationID, entry.ResolutionPath = stored[0].Association, &path
		}
		if err := appendModelAudit(ctx, t.tx, entry); err != nil {
			return err
		}
	}
	result, err := t.tx.ExecContext(ctx, `UPDATE `+m.table+` SET archived=TRUE WHERE `+m.key+`=$1`, id)
	if err != nil {
		return err
	}
	if affected, err := result.RowsAffected(); err != nil || affected == 0 {
		return errors.Join(errArchiveNoRows, err)
	}
	return nil
}

func (t *subscriptionArchiveTransaction) CompleteJob(ctx context.Context, id, token uuid.UUID) error {
	return completeJob(ctx, t.tx, id, token, nil)
}
func (t *subscriptionArchiveTransaction) Commit() error   { return t.ownedTransaction.Commit() }
func (t *subscriptionArchiveTransaction) Rollback() error { return t.ownedTransaction.Rollback() }
