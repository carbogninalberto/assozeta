package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

type SubscriptionAutoArchives struct{ db *sqlx.DB }

func NewSubscriptionAutoArchives(db *sqlx.DB) *SubscriptionAutoArchives {
	return &SubscriptionAutoArchives{db: db}
}
func (r *SubscriptionAutoArchives) BeginSubscriptionArchive(ctx context.Context) (domain.SubscriptionArchiveTransaction, error) {
	tx, err := r.db.BeginTxx(ctx, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &subscriptionArchiveTransaction{tx: tx}, nil
}

type subscriptionArchiveTransaction struct{ tx *sqlx.Tx }

func (t *subscriptionArchiveTransaction) ReadArchivePage(ctx context.Context, after *uuid.UUID) ([]domain.SubscriptionArchiveCandidate, error) {
	var rows []struct {
		ID, AssociationID                                                        uuid.UUID
		PaymentID                                                                uuid.NullUUID
		Created                                                                  time.Time
		StartDate, EndDate                                                       sql.NullTime
		FiscalDay, FiscalMonth, StartDay, StartMonth, EndDay, EndMonth, Duration int
		CustomEnd                                                                bool
	}
	err := t.tx.SelectContext(ctx, &rows, subscriptionArchivePageSQL, after, domain.SubscriptionArchiveBatchLimit)
	if err != nil {
		return nil, err
	}
	date := func(d sql.NullTime) *domain.CalendarDate {
		if !d.Valid {
			return nil
		}
		y, m, day := d.Time.Date()
		return &domain.CalendarDate{Year: y, Month: int(m), Day: day}
	}
	result := make([]domain.SubscriptionArchiveCandidate, len(rows))
	for i, row := range rows {
		c := domain.SubscriptionArchiveCandidate{ID: row.ID, AssociationID: row.AssociationID, Created: row.Created.UTC(),
			Fiscal: domain.CalendarRule{StartDay: row.FiscalDay, StartMonth: row.FiscalMonth},
			Rule:   domain.SubscriptionDateRule{Duration: row.Duration, Season: domain.CalendarRule{StartDay: row.StartDay, StartMonth: row.StartMonth, CustomEnd: row.CustomEnd, EndDay: row.EndDay, EndMonth: row.EndMonth}},
			Dates:  domain.SubscriptionDates{Start: date(row.StartDate), End: date(row.EndDate)}}
		if row.PaymentID.Valid {
			id := row.PaymentID.UUID
			c.PaymentID = &id
		}
		result[i] = c
	}
	return result, nil
}

const subscriptionArchivePageSQL = `SELECT s.subscription_id AS id,s.sport_association_id AS associationid,s.payment_id AS paymentid,
 s.creation_date AS created,s.start_date AS startdate,s.end_date AS enddate,
 u.balance_sheet_start_day AS fiscalday,u.balance_sheet_start_month AS fiscalmonth,
 u.subscription_start_day AS startday,u.subscription_start_month AS startmonth,
 u.subscription_end_day AS endday,u.subscription_end_month AS endmonth,u.custom_end_date AS customend,u.subscription_duration AS duration
 FROM application_subscription s
 JOIN application_sportassociation a ON a.sport_association_id=s.sport_association_id
 JOIN bakney_user u ON u.user_id=a.user_id
 WHERE NOT s.archived AND NOT s.deleted AND u.auto_archive AND ($1::uuid IS NULL OR s.subscription_id>$1)
 ORDER BY s.subscription_id LIMIT $2 FOR UPDATE OF s FOR SHARE OF a,u`

type archiveReference struct {
	ID            uuid.UUID `json:"id"`
	AssociationID uuid.UUID `json:"association"`
}

func (t *subscriptionArchiveTransaction) LockArchivePayments(ctx context.Context, refs []domain.PaymentSaveReference) error {
	unique, err := domain.UniquePaymentSaveReferences(refs)
	if err != nil {
		return err
	}
	if len(unique) == 0 {
		return nil
	}
	desired := make([]archiveReference, len(unique))
	for i, r := range unique {
		desired[i] = archiveReference{r.PaymentID, r.AssociationID}
	}
	raw, err := json.Marshal(desired)
	if err != nil {
		return err
	}
	var ids []uuid.UUID
	err = t.tx.SelectContext(ctx, &ids, `SELECT p.payment_id FROM application_payment p
 JOIN jsonb_to_recordset($1::jsonb) AS r(id uuid,association uuid) ON p.payment_id=r.id AND p.sport_association_id=r.association
 ORDER BY p.payment_id FOR UPDATE OF p`, raw)
	if err != nil {
		return err
	}
	if len(ids) != len(unique) {
		return domain.ErrPaymentSaveScope
	}
	return nil
}
func (t *subscriptionArchiveTransaction) ReadPaymentSaveInputs(ctx context.Context, refs []domain.PaymentSaveReference, category string) ([]domain.PaymentSaveState, error) {
	return NewPaymentSaveInputs(t.tx).ReadPaymentSaveInputs(ctx, refs, category)
}
func (t *subscriptionArchiveTransaction) ArchiveSubscriptions(ctx context.Context, plans []domain.SubscriptionArchivePlan, at time.Time) error {
	type desired struct {
		archiveReference
		Start *string `json:"start"`
		End   *string `json:"end"`
	}
	date := func(d *domain.CalendarDate) *string {
		if d == nil {
			return nil
		}
		s := fmt.Sprintf("%04d-%02d-%02d", d.Year, d.Month, d.Day)
		return &s
	}
	rows := make([]desired, len(plans))
	for i, p := range plans {
		rows[i] = desired{archiveReference{p.ID, p.AssociationID}, date(p.Dates.Start), date(p.Dates.End)}
	}
	return t.writeArchive(ctx, archiveSubscriptionsSQL, rows, len(rows), at, "subscription", "Subscription")
}
func (t *subscriptionArchiveTransaction) ArchivePayments(ctx context.Context, plans []domain.PaymentSavePlan, at time.Time) error {
	type desired struct {
		archiveReference
		Category    *uuid.UUID `json:"category"`
		Description *string    `json:"description"`
	}
	rows := make([]desired, len(plans))
	for i, p := range plans {
		rows[i] = desired{archiveReference{p.Payment.ID, p.Payment.AssociationID}, p.Payment.CategoryID, p.Description}
	}
	return t.writeArchive(ctx, archivePaymentsSQL, rows, len(rows), at, "payment", "Payment")
}
func (t *subscriptionArchiveTransaction) ArchiveInvoices(ctx context.Context, refs []domain.ArchiveInvoiceReference, at time.Time) error {
	rows := make([]archiveReference, len(refs))
	for i, r := range refs {
		rows[i] = archiveReference{r.ID, r.AssociationID}
	}
	return t.writeArchive(ctx, archiveInvoicesSQL, rows, len(rows), at, "invoice", "Invoice")
}
func (t *subscriptionArchiveTransaction) writeArchive(ctx context.Context, prefix string, rows any, n int, at time.Time, model, label string) error {
	if n == 0 {
		return nil
	}
	if n > domain.SubscriptionArchiveBatchLimit {
		return domain.ErrPaymentSaveBatch
	}
	raw, err := json.Marshal(rows)
	if err != nil {
		return err
	}
	var counts struct{ Matched, Changed, Logged, Indexed int }
	if err = t.tx.GetContext(ctx, &counts, prefix+archiveAuditSQL, raw, at, model, label); err != nil {
		return err
	}
	if counts.Matched != n {
		return domain.ErrPaymentSaveScope
	}
	if counts.Changed != counts.Logged || counts.Logged != counts.Indexed {
		return errors.New("subscription archive audit/index count mismatch")
	}
	return nil
}

// Each UPDATE touches only the archive/default fields. Audit deltas use locked,
// actual stored values, including soft-deleted rows; monetary fields never round-trip.
const archiveSubscriptionsSQL = `WITH desired AS (
 SELECT * FROM jsonb_to_recordset($1::jsonb) AS r(id uuid,association uuid,start date,"end" date)
), originals AS MATERIALIZED (
 SELECT s.subscription_id AS id,s.sport_association_id AS association,s.archived,s.start_date,s.end_date,d.start AS new_start,d."end" AS new_end
 FROM application_subscription s JOIN desired d ON d.id=s.subscription_id AND d.association=s.sport_association_id
 ORDER BY s.subscription_id FOR UPDATE OF s
), changed AS (
 UPDATE application_subscription s SET archived=TRUE,start_date=o.new_start,end_date=o.new_end FROM originals o
 WHERE s.subscription_id=o.id AND (NOT o.archived OR o.start_date IS DISTINCT FROM o.new_start OR o.end_date IS DISTINCT FROM o.new_end)
 RETURNING o.id,o.association,jsonb_strip_nulls(jsonb_build_object(
 'archived',CASE WHEN NOT o.archived THEN '["False","True"]'::jsonb END,
 'start_date',CASE WHEN o.start_date IS DISTINCT FROM o.new_start THEN jsonb_build_array(COALESCE(o.start_date::text,'None'),COALESCE(o.new_start::text,'None')) END,
 'end_date',CASE WHEN o.end_date IS DISTINCT FROM o.new_end THEN jsonb_build_array(COALESCE(o.end_date::text,'None'),COALESCE(o.new_end::text,'None')) END)) AS changes
)`
const archivePaymentsSQL = `WITH desired AS (
 SELECT * FROM jsonb_to_recordset($1::jsonb) AS r(id uuid,association uuid,category uuid,description text)
), originals AS MATERIALIZED (
 SELECT p.payment_id AS id,p.sport_association_id AS association,p.archived,p.payment_category_id,p.description,
 d.category AS new_category,d.description AS new_description
 FROM application_payment p JOIN desired d ON d.id=p.payment_id AND d.association=p.sport_association_id
 ORDER BY p.payment_id FOR UPDATE OF p
), changed AS (
 UPDATE application_payment p SET archived=TRUE,payment_category_id=o.new_category,description=o.new_description FROM originals o
 WHERE p.payment_id=o.id AND (NOT o.archived OR o.payment_category_id IS DISTINCT FROM o.new_category OR o.description IS DISTINCT FROM o.new_description)
 RETURNING o.id,o.association,jsonb_strip_nulls(jsonb_build_object(
 'archived',CASE WHEN NOT o.archived THEN '["False","True"]'::jsonb END,
 'payment_category',CASE WHEN o.payment_category_id IS DISTINCT FROM o.new_category THEN jsonb_build_array(COALESCE(o.payment_category_id::text,'None'),COALESCE(o.new_category::text,'None')) END,
 'description',CASE WHEN o.description IS DISTINCT FROM o.new_description THEN jsonb_build_array(COALESCE(o.description,'None'),COALESCE(o.new_description,'None')) END)) AS changes
)`
const archiveInvoicesSQL = `WITH desired AS (
 SELECT * FROM jsonb_to_recordset($1::jsonb) AS r(id uuid,association uuid)
), originals AS MATERIALIZED (
 SELECT i.invoice_id AS id,i.sport_association_id AS association,i.archived
 FROM application_invoice i JOIN desired d ON d.id=i.invoice_id AND d.association=i.sport_association_id
 ORDER BY i.invoice_id FOR UPDATE OF i
), changed AS (
 UPDATE application_invoice i SET archived=TRUE FROM originals o WHERE i.invoice_id=o.id AND NOT o.archived
 RETURNING o.id,o.association,'{"archived":["False","True"]}'::jsonb AS changes
)`
const archiveAuditSQL = `, logged AS (
 INSERT INTO auditlog_logentry
 (content_type_id,object_pk,object_id,object_repr,serialized_data,action,changes_text,changes,actor_id,cid,remote_addr,remote_port,timestamp,additional_data,actor_email)
 SELECT ct.id,c.id::text,NULL,$4||' object ('||c.id::text||')',NULL,1,'',c.changes,NULL,NULL,NULL,NULL,$2,NULL,NULL
 FROM changed c CROSS JOIN django_content_type ct WHERE ct.app_label='application' AND ct.model=$3
 RETURNING id,object_pk
), indexed AS (
 INSERT INTO application_auditlogindex (audit_log_index_id,log_entry_id,sport_association_id,resolution_path,created_at)
 SELECT gen_random_uuid(),l.id,c.association,'direct',$2 FROM logged l JOIN changed c ON c.id::text=l.object_pk
 RETURNING audit_log_index_id
)
SELECT (SELECT count(*) FROM originals) AS matched,(SELECT count(*) FROM changed) AS changed,
 (SELECT count(*) FROM logged) AS logged,(SELECT count(*) FROM indexed) AS indexed`

func (t *subscriptionArchiveTransaction) CompleteJob(ctx context.Context, id, token uuid.UUID) error {
	return completeJob(ctx, t.tx, id, token, nil)
}
func (t *subscriptionArchiveTransaction) Commit() error   { return t.tx.Commit() }
func (t *subscriptionArchiveTransaction) Rollback() error { return t.tx.Rollback() }
