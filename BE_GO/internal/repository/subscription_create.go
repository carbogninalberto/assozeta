package repository

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// SubscriptionCreates persists create_subscription (subscription-create.md).
type SubscriptionCreates struct{ db *sqlx.DB }

func NewSubscriptionCreates(db *sqlx.DB) *SubscriptionCreates { return &SubscriptionCreates{db: db} }

type subscriptionCreateTransaction struct{ *subscriptionEditTransaction }

func (r *SubscriptionCreates) BeginSubscriptionCreate(ctx context.Context) (domain.SubscriptionCreateTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &subscriptionCreateTransaction{&subscriptionEditTransaction{ownedTransaction: tx}}, nil
}

func createDateText(value domain.CalendarDate) string {
	return fmt.Sprintf("%04d-%02d-%02d", value.Year, value.Month, value.Day)
}

func (t *subscriptionCreateTransaction) Settings(ctx context.Context, user string) (*domain.SubscriptionCreateSettings, int, error) {
	rows := []domain.SubscriptionCreateSettings{}
	if err := t.tx.SelectContext(ctx, &rows, `SELECT a.sport_association_id, u.user_id, a.enable_quotes_management,
 a.subscription_fee::text AS subscription_fee, a.membership_fee::text AS membership_fee, a.multiple_subscription_fee, a.multiple_membership_fee,
 a.subscription_fee_plans::text AS subscription_fee_plans, a.membership_fee_plans::text AS membership_fee_plans,
 u.subscription_start_day, u.subscription_start_month, u.custom_end_date, u.subscription_end_day, u.subscription_end_month,
 u.subscription_duration, u.membership_duration, u.membership_starting_number, u.default_membership_type, u.show_zero_payments,
 u.default_payment_category_id
 FROM application_sportassociation a JOIN bakney_user u ON u.user_id=$1
 WHERE a.user_id=$1 AND NOT a.deleted ORDER BY a.sport_association_id`, user); err != nil {
		return nil, 0, err
	}
	if len(rows) != 1 {
		return nil, len(rows), nil
	}
	// SportAssociation.objects.select_for_update().filter(...).first()
	if _, err := t.tx.ExecContext(ctx, `SELECT 1 FROM application_sportassociation WHERE sport_association_id=$1 FOR UPDATE`,
		rows[0].AssociationID); err != nil {
		return nil, 0, err
	}
	return &rows[0], 1, nil
}

// iexact is Django's UPPER(col::text) = UPPER(value), or IS NULL for None.
func iexact(column string, position int) string {
	return fmt.Sprintf(`(($%[2]d::text IS NULL AND %[1]s IS NULL) OR UPPER(%[1]s::text) = UPPER($%[2]d::text))`, column, position)
}

func (t *subscriptionCreateTransaction) Existing(ctx context.Context, association string, first, last, tax *string,
	start, end domain.CalendarDate) (*domain.SubscriptionCreateExisting, error) {
	var row domain.SubscriptionCreateExisting
	err := t.tx.GetContext(ctx, &row, `SELECT s.subscription_id, s.start_date, s.end_date, s.associate_id, a.tax_code
 FROM application_subscription s INNER JOIN application_associate a ON a.associate_id=s.associate_id
 WHERE s.sport_association_id=$1 AND `+iexact("a.first_name", 2)+` AND `+iexact("a.last_name", 3)+` AND `+iexact("a.tax_code", 4)+`
 AND s.end_date >= $5::date AND s.start_date <= $6::date AND NOT s.archived ORDER BY s.subscription_id LIMIT 1`,
		association, first, last, tax, createDateText(start), createDateText(end))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *subscriptionCreateTransaction) FutureRenewal(ctx context.Context, association string, end domain.CalendarDate, tax *string) (bool, error) {
	return t.exists(ctx, `SELECT 1 FROM application_subscription s INNER JOIN application_associate a ON a.associate_id=s.associate_id
 WHERE s.sport_association_id=$1 AND s.start_date >= $2::date AND NOT s.archived AND `+iexact("a.tax_code", 3), association, createDateText(end), tax)
}

func (t *subscriptionCreateTransaction) Certificate(ctx context.Context, id string) (bool, *string, error) {
	var user *string
	err := t.tx.GetContext(ctx, &user, `SELECT user_id FROM application_medicalcertificate WHERE medical_id=$1 FOR UPDATE`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil, nil
	}
	return err == nil, user, err
}

func (t *subscriptionCreateTransaction) SaveCertificateUser(ctx context.Context, id, user string) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_medicalcertificate SET user_id=$2 WHERE medical_id=$1`, id, user)
	return err
}

// Category is the default payment category by key, or the first category
// named 'entrate e proventi da attività tipiche' (iexact, by key).
func (t *subscriptionCreateTransaction) Category(ctx context.Context, id *string) (*string, error) {
	var found string
	var err error
	if id != nil {
		err = t.tx.GetContext(ctx, &found, `SELECT payment_category_id FROM application_paymentcategory WHERE payment_category_id=$1`, *id)
	} else {
		err = t.tx.GetContext(ctx, &found, `SELECT payment_category_id FROM application_paymentcategory
 WHERE UPPER(name::text)=UPPER('entrate e proventi da attività tipiche') ORDER BY payment_category_id LIMIT 1`)
	}
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &found, nil
}

func (t *subscriptionCreateTransaction) DefaultBankAccount(ctx context.Context, association string) (*string, error) {
	var found string
	err := t.tx.GetContext(ctx, &found, `SELECT custom_account_id FROM application_customaccounts
 WHERE sport_association_id=$1 AND NOT editable AND enabled AND account_type=2 ORDER BY custom_account_id LIMIT 1`, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &found, nil
}

// ReusablePayments is the newest unpaid, live subscription payment of the
// associate with this amount and payment day; tied reports several newest.
func (t *subscriptionCreateTransaction) ReusablePayments(ctx context.Context, associate, association, amount string, day time.Time) ([]string, bool, error) {
	rows := []struct {
		ID      string    `db:"payment_id"`
		Created time.Time `db:"creation_date"`
	}{}
	if err := t.tx.SelectContext(ctx, &rows, `SELECT payment_id, creation_date FROM application_payment
 WHERE associate_id=$1 AND sport_association_id=$2 AND subject=1 AND NOT paid AND amount=$3::numeric AND payment_date=$4 AND NOT deleted
 ORDER BY creation_date DESC LIMIT 2 FOR UPDATE`, associate, association, amount, day); err != nil {
		return nil, false, err
	}
	ids := make([]string, 0, len(rows))
	for _, row := range rows {
		ids = append(ids, row.ID)
	}
	return ids, len(rows) == 2 && rows[0].Created.Equal(rows[1].Created), nil
}

func (t *subscriptionCreateTransaction) PaymentAmount(ctx context.Context, id string) (string, error) {
	var amount string
	err := t.tx.GetContext(ctx, &amount, `SELECT amount::text FROM application_payment WHERE payment_id=$1`, id)
	return amount, err
}

func (t *subscriptionCreateTransaction) InsertPayment(ctx context.Context, row domain.SubscriptionCreatePayment) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_payment (payment_id, type, subject, amount, creation_date, payment_date, paid,
 expense, description, payment_category_id, meta_payment_categories, invoice_id, user_id, associate_id, supplier_id, instructor_id,
 sport_association_id, payment_intent_id, archived, invoice_fiscal_code, meta, notes, custom_accounts_id, signature_id, deleted, group_id,
 course) VALUES ($1,'default',1,$2::numeric,$3,$4,false,false,'Iscrizione',$5,$6::jsonb,NULL,$7,$8,NULL,NULL,$9,NULL,false,NULL,$10::jsonb,NULL,
 $11,NULL,false,NULL,NULL)`, row.ID, row.Amount, row.Created, row.PaymentDate, row.CategoryID, row.MetaCategories, row.UserID, row.AssociateID,
		row.AssociationID, row.Meta, row.AccountID)
	return err
}

func (t *subscriptionCreateTransaction) LatestNumber(ctx context.Context, association string) (*string, error) {
	var number string
	err := t.tx.GetContext(ctx, &number, `SELECT subscription_number FROM application_subscription
 WHERE sport_association_id=$1 AND subscription_number IS NOT NULL AND subscription_number::text ~ '^\d+$'
 ORDER BY (subscription_number)::bigint DESC LIMIT 1`, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &number, nil
}

func (t *subscriptionCreateTransaction) InsertSubscription(ctx context.Context, row domain.SubscriptionCreateRow) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_subscription (subscription_id, sport_association_id, associate_id, supplier_id,
 instructor_id, custom_data, additional_fields, user_id, type, role, acceptance_date, resignation_date, medical_id, status_flag, creation_date,
 start_date, end_date, signature_url, signature_storage_key, document_pdf_id, payment_id, deleted, archived, draft, trial, meta,
 subscription_number, subscription_type, group_id, competitive, notes)
 VALUES ($1,$2,$3,NULL,NULL,$4::jsonb,$5::jsonb,$6,$7,$8,NULL,NULL,$9,$10,$11,$12::date,$13::date,NULL,NULL,NULL,$14,false,false,true,false,
 $15::jsonb,$16,$17,NULL,false,NULL)`, row.ID, row.AssociationID, row.AssociateID, row.CustomData, row.AdditionalFields, row.UserID, row.Type,
		row.Role, row.MedicalID, row.Status, row.Created, calendarValue(row.Start), calendarValue(row.End), row.PaymentID, row.Meta,
		row.Number, row.SubscriptionType)
	return err
}

func (t *subscriptionCreateTransaction) InsertMembership(ctx context.Context, row domain.SubscriptionCreateMembership) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_subscriptionmembership (subscription_membership_id, subscription_id,
 sport_association_id, creation_date, start_date, end_date, membership_type, membership_number, description, price, payment_id, associate_id)
 VALUES ($1,$2,$3,$4,$5::date,$6::date,$7,$8,NULL,$9::numeric,$10,$11)`, row.ID, row.SubscriptionID, row.AssociationID, row.Created,
		calendarValue(row.Start), calendarValue(row.End), row.Type, row.Number, row.Price, row.PaymentID, row.AssociateID)
	return err
}

func (t *subscriptionCreateTransaction) InsertRelation(ctx context.Context, associate, tutor string) (int64, error) {
	var id int64
	err := t.tx.GetContext(ctx, &id, `INSERT INTO application_associatetutorrelation (associate_id, tutor_id, is_primary)
 VALUES ($1,$2,true) RETURNING id`, associate, tutor)
	return id, err
}

func (t *subscriptionCreateTransaction) AuditEntry(ctx context.Context, entry domain.SubscriptionCreateAudit) error {
	return appendSubscriptionCreateAudit(ctx, t.tx, entry)
}

func appendSubscriptionCreateAudit(ctx context.Context, tx *sqlx.Tx, entry domain.SubscriptionCreateAudit) error {
	changes := entry.Changes
	if entry.Created {
		var err error
		if changes, err = createdModelChanges(ctx, tx, entry.Table, entry.Column, entry.Key); err != nil {
			return err
		}
		for name, pair := range entry.Overrides {
			changes[name] = pair
		}
	}
	event := modelAuditEntry{Model: entry.Model, Key: entry.Key, Representation: entry.Representation + " object (" + entry.Key + ")",
		Action: entry.Action, Changes: changes, Context: entry.Context, At: entry.At}
	if entry.Association != nil {
		path := entry.Path
		event.AssociationID, event.ResolutionPath = entry.Association, &path
	}
	return appendModelAuditForApp(ctx, tx, entry.App, event)
}

// SubscriptionNotifications persists NotificationManager.validate_broadcast's
// rows, each create in its own (autocommitted) transaction with its entry.
type SubscriptionNotifications struct{ db *sqlx.DB }

func NewSubscriptionNotifications(db *sqlx.DB) *SubscriptionNotifications {
	return &SubscriptionNotifications{db: db}
}

func (r *SubscriptionNotifications) ValidateBroadcast(ctx context.Context, user, name string, audit domain.LoginAuditContext, at time.Time) (bool, error) {
	broadcast, err := r.broadcast(ctx, name, audit, at)
	if err != nil {
		return false, err
	}
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return false, err
	}
	defer tx.Rollback()
	var count int
	if err = tx.tx.GetContext(ctx, &count, `SELECT COUNT(*) FROM notifications_usernotificationsbroadcast WHERE broadcast_id=$1 AND user_id=$2`,
		broadcast, user); err != nil {
		return false, err
	}
	if count != 0 {
		return false, tx.Commit()
	}
	id := uuid.NewString()
	if _, err = tx.tx.ExecContext(ctx, `INSERT INTO notifications_usernotificationsbroadcast (user_broadcast_id, user_id, broadcast_id)
 VALUES ($1,$2,$3)`, id, user, broadcast); err != nil {
		return false, err
	}
	if err = notificationAudit(ctx, tx.tx, "usernotificationsbroadcast", "UserNotificationsBroadcast", "notifications_usernotificationsbroadcast",
		"user_broadcast_id", id, audit, at); err != nil {
		return false, err
	}
	return true, tx.Commit()
}

// broadcast is NotificationsBroadcast.objects.filter(broadcast_name=...).last(),
// created when missing.
func (r *SubscriptionNotifications) broadcast(ctx context.Context, name string, audit domain.LoginAuditContext, at time.Time) (string, error) {
	var found string
	err := r.db.GetContext(ctx, &found, `SELECT broadcast_id FROM notifications_notificationsbroadcast WHERE broadcast_name=$1
 ORDER BY broadcast_id DESC LIMIT 1`, name)
	if err == nil {
		return found, nil
	}
	if !errors.Is(err, sql.ErrNoRows) {
		return "", err
	}
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return "", err
	}
	defer tx.Rollback()
	id := uuid.NewString()
	if _, err = tx.tx.ExecContext(ctx, `INSERT INTO notifications_notificationsbroadcast (broadcast_id, broadcast_name) VALUES ($1,$2)`, id, name); err != nil {
		return "", err
	}
	if err = notificationAudit(ctx, tx.tx, "notificationsbroadcast", "NotificationsBroadcast", "notifications_notificationsbroadcast",
		"broadcast_id", id, audit, at); err != nil {
		return "", err
	}
	return id, tx.Commit()
}

// notificationAudit is the create entry, indexed through the actor's live
// association (no resolver, no sport_association in the changes).
func notificationAudit(ctx context.Context, tx *sqlx.Tx, model, representation, table, column, id string, audit domain.LoginAuditContext, at time.Time) error {
	var association *string
	if audit.ActorID != nil {
		var found string
		err := tx.GetContext(ctx, &found, `SELECT sport_association_id FROM application_sportassociation WHERE user_id=$1 AND NOT deleted
 ORDER BY sport_association_id LIMIT 1`, *audit.ActorID)
		if err == nil {
			association = &found
		} else if !errors.Is(err, sql.ErrNoRows) {
			return err
		}
	}
	return appendSubscriptionCreateAudit(ctx, tx, domain.SubscriptionCreateAudit{App: "notifications", Model: model, Representation: representation,
		Table: table, Column: column, Key: id, Action: 0, Created: true, Association: association, Path: "actor.sport_association",
		Context: audit, At: at})
}
