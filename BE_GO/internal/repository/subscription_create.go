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
 u.default_payment_category_id, u.default_payment_category_courses_id, a.denomination, u.email AS owner_email
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
	start, end domain.CalendarDate, custom *string) (*domain.SubscriptionCreateExisting, error) {
	var pattern *string
	if custom != nil {
		// custom_data__icontains: UPPER(custom_data::text) LIKE UPPER('%value%') with the LIKE metacharacters escaped.
		escaped := paymentReadsContains(*custom)
		pattern = &escaped
	}
	var row domain.SubscriptionCreateExisting
	err := t.tx.GetContext(ctx, &row, `SELECT s.subscription_id, s.start_date, s.end_date, s.associate_id, a.tax_code
 FROM application_subscription s INNER JOIN application_associate a ON a.associate_id=s.associate_id
 WHERE s.sport_association_id=$1 AND `+iexact("a.first_name", 2)+` AND `+iexact("a.last_name", 3)+` AND `+iexact("a.tax_code", 4)+`
 AND s.end_date >= $5::date AND s.start_date <= $6::date AND NOT s.archived
 AND ($7::text IS NULL OR UPPER(s.custom_data::text) LIKE UPPER($7::text)) ORDER BY s.subscription_id LIMIT 1`,
		association, first, last, tax, createDateText(start), createDateText(end), pattern)
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

func (t *subscriptionCreateTransaction) LinkableCertificate(ctx context.Context, id, association string, accounts []string) (bool, *string, error) {
	var user *string
	err := t.tx.GetContext(ctx, &user, `SELECT m.user_id FROM application_medicalcertificate m WHERE m.medical_id=$1 AND (
 m.user_id = ANY($3::uuid[])
 OR EXISTS (SELECT 1 FROM application_subscription s WHERE s.medical_id=m.medical_id AND s.sport_association_id=$2)
 OR (m.user_id IS NULL AND NOT EXISTS (SELECT 1 FROM application_subscription s WHERE s.medical_id=m.medical_id
  AND s.sport_association_id IS DISTINCT FROM $2::uuid))) FOR UPDATE OF m`, id, association, accounts)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil, nil
	}
	return err == nil, user, err
}

func (t *subscriptionCreateTransaction) SaveCertificateUser(ctx context.Context, id, user string) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_medicalcertificate SET user_id=$2 WHERE medical_id=$1`, id, user)
	return err
}

func (t *subscriptionCreateTransaction) CertificateExpiration(ctx context.Context, id string) (*time.Time, error) {
	var date *time.Time
	err := t.tx.GetContext(ctx, &date, `SELECT expiration_date FROM application_medicalcertificate WHERE medical_id=$1`, id)
	return date, err
}

func (t *subscriptionCreateTransaction) SaveCertificate(ctx context.Context, id, user string, expiration *time.Time) error {
	if expiration == nil {
		return t.SaveCertificateUser(ctx, id, user)
	}
	_, err := t.tx.ExecContext(ctx, `UPDATE application_medicalcertificate SET user_id=$2, expiration_date=$3::date WHERE medical_id=$1`,
		id, user, expiration.Format("2006-01-02"))
	return err
}

// Category is the default payment category by key, or
// PaymentCategory.default_for(association).
func (t *subscriptionCreateTransaction) Category(ctx context.Context, id *string, association string) (*string, error) {
	var found string
	var err error
	if id != nil {
		err = t.tx.GetContext(ctx, &found, `SELECT payment_category_id FROM application_paymentcategory WHERE payment_category_id=$1`, *id)
	} else {
		var row struct {
			ID   string `db:"payment_category_id"`
			Name string `db:"name"`
		}
		err = t.tx.GetContext(ctx, &row, secCategoryDefaultSQL, association)
		found = row.ID
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
 $11,NULL,false,NULL,NULL)`, row.ID, row.Amount, row.Created, subscriptionCreatePaymentDate(row.PaymentDate), row.CategoryID, row.MetaCategories, row.UserID, row.AssociateID,
		row.AssociationID, row.Meta, row.AccountID)
	return err
}

// subscriptionCreatePaymentDate stores a zero payment date as NULL (no payment_date given).
func subscriptionCreatePaymentDate(value time.Time) any {
	if value.IsZero() {
		return nil
	}
	return value
}

func (t *subscriptionCreateTransaction) QuickAssociate(ctx context.Context, id string) (*domain.SubscriptionEditAssociate, error) {
	return t.oneAssociate(ctx, `associate_id=$1 AND NOT deleted`, id)
}

func (t *subscriptionCreateTransaction) QuickAssociationExists(ctx context.Context, id string) (bool, error) {
	return t.exists(ctx, `SELECT 1 FROM application_sportassociation WHERE sport_association_id=$1 AND NOT deleted`, id)
}

func (t *subscriptionCreateTransaction) QuickCertificates(ctx context.Context, associate string) ([]domain.SubscriptionQuickCertificate, error) {
	rows := []domain.SubscriptionQuickCertificate{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT m.medical_id, m.expiration_date FROM application_subscription s
 JOIN application_medicalcertificate m ON m.medical_id=s.medical_id WHERE s.associate_id=$1`, associate)
	return rows, err
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

func (t *subscriptionCreateTransaction) GroupedCustomData(ctx context.Context, user string) ([]domain.SubscriptionGroupedCustomData, error) {
	rows := []domain.SubscriptionGroupedCustomData{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT custom_data::text AS custom_data, creation_date FROM application_subscription
 WHERE user_id=$1 AND (UPPER(custom_data::text) LIKE UPPER('%asd-o-ssd%') OR UPPER(custom_data::text) LIKE UPPER('%scuola-asc%'))
 ORDER BY creation_date DESC LIMIT 2`, user)
	return rows, err
}

func (t *subscriptionCreateTransaction) RenewalSource(ctx context.Context, id, association string) (*domain.SubscriptionRenewalSource, error) {
	rows := []domain.SubscriptionRenewalSource{}
	if err := t.tx.SelectContext(ctx, &rows, `SELECT s.user_id, a.user_id AS owner_id, s.associate_id, s.medical_id, s.role, s.subscription_number,
 s.subscription_type, s.custom_data::text AS custom_data, s.additional_fields::text AS additional_fields
 FROM application_subscription s JOIN application_sportassociation a ON a.sport_association_id=s.sport_association_id
 WHERE s.subscription_id=$1 AND s.sport_association_id=$2`, id, association); err != nil || len(rows) == 0 {
		return nil, err
	}
	return &rows[0], nil
}

func (t *subscriptionCreateTransaction) InsertSubscription(ctx context.Context, row domain.SubscriptionCreateRow) error {
	user := &row.UserID
	if row.Renewal {
		user = row.RenewedUser
	}
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_subscription (subscription_id, sport_association_id, associate_id, supplier_id,
 instructor_id, custom_data, additional_fields, user_id, type, role, acceptance_date, resignation_date, medical_id, status_flag, creation_date,
 start_date, end_date, signature_url, signature_storage_key, document_pdf_id, payment_id, deleted, archived, draft, trial, meta,
 subscription_number, subscription_type, group_id, competitive, notes)
 VALUES ($1,$2,$3,NULL,NULL,$4::jsonb,$5::jsonb,$6,$7,$8,NULL,NULL,$9,$10,$11,$12::date,$13::date,NULL,NULL,NULL,$14,false,false,true,false,
 $15::jsonb,$16,$17,NULL,false,NULL)`, row.ID, row.AssociationID, row.AssociateID, row.CustomData, row.AdditionalFields, user, row.Type,
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

// insertPlaceholderDocument is Document.objects.create(filename=...) of a
// generated placeholder certificate, with its create entry.
func insertPlaceholderDocument(ctx context.Context, tx *sqlx.Tx, document domain.NewDocument, actorAssociation *string,
	audit domain.LoginAuditContext, at time.Time) error {
	if _, err := tx.ExecContext(ctx, `INSERT INTO docmanager_document (document_id, creation_date, filepath, filename, file_size_bytes, token)
 VALUES ($1, $2, NULL, $3, NULL, $4)`, document.ID, document.Created, document.Filename, document.Token); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, tx, "docmanager_document", "document_id", document.ID)
	if err != nil {
		return err
	}
	for name, rendered := range documentReverse {
		changes[name] = [2]string{"None", rendered}
	}
	return appendModelAuditForApp(ctx, tx, "docmanager", documentAudit(document.ID, 0, changes, actorAssociation, audit, at))
}

func (t *subscriptionCreateTransaction) CreatePlaceholderDocument(ctx context.Context, document domain.NewDocument, actorAssociation *string,
	audit domain.LoginAuditContext, at time.Time) error {
	return insertPlaceholderDocument(ctx, t.tx, document, actorAssociation, audit, at)
}

func (t *subscriptionCreateTransaction) InsertPlaceholderCertificate(ctx context.Context, id, user, document string, expiration,
	created time.Time) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_medicalcertificate (medical_id, document_id, user_id, creation_date,
 expiration_date, competitive_medical_certificate, notes) VALUES ($1, $2, $3, $4, $5::date, FALSE, NULL)`, id, document, user, created,
		expiration.Format("2006-01-02"))
	return err
}

// --- signatures and new member accounts (subscription-create.md) ---

func (t *subscriptionCreateTransaction) InsertSignature(ctx context.Context, id, user, data string, audit domain.LoginAuditContext,
	at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_signature (signature_id, user_id, signature) VALUES ($1, $2, $3)`,
		id, user, data); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_signature", "signature_id", id)
	if err != nil {
		return err
	}
	// Signature has no association to resolve: the actor's own association, if any.
	entry := modelAuditEntry{Model: "signature", Key: id, Representation: "Signature object (" + id + ")", Action: 0, Changes: changes,
		Context: audit, At: at}
	if err = actorIndex(ctx, t.tx, audit, &entry); err != nil {
		return err
	}
	return appendModelAudit(ctx, t.tx, entry)
}

func (t *subscriptionCreateTransaction) InsertFamily(ctx context.Context, id, kind string, audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_family (family_id, type) VALUES ($1, $2)`, id, kind); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_family", "family_id", id)
	if err != nil {
		return err
	}
	// Family has no association to resolve: the actor's own association, if any.
	entry := modelAuditEntry{Model: "family", Key: id, Representation: "Family object (" + id + ")", Action: 0, Changes: changes,
		Context: audit, At: at}
	if err = actorIndex(ctx, t.tx, audit, &entry); err != nil {
		return err
	}
	return appendModelAudit(ctx, t.tx, entry)
}

func (t *subscriptionCreateTransaction) RenderPayment(ctx context.Context, id string) (domain.PaymentStateRender, error) {
	courses := &courseSubscriptionTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: t.ownedTransaction}}
	payment, err := courses.payment(ctx, id, nil)
	if err != nil {
		return domain.PaymentStateRender{}, err
	}
	render := domain.PaymentStateRender{Payment: payment}
	render.Instructor, render.Invoice, err = paymentNestedRows(ctx, t.tx, payment.InvoicePartyPayment)
	return render, err
}

func (t *subscriptionCreateTransaction) SaveSubscriptionSignature(ctx context.Context, id, association string, url, key *string,
	audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_subscription SET signature_url=$2, signature_storage_key=$3 WHERE subscription_id=$1`,
		id, url, key); err != nil {
		return err
	}
	changes := map[string][2]string{}
	if url != nil {
		changes["signature_url"] = [2]string{"None", *url}
	}
	if key != nil {
		changes["signature_storage_key"] = [2]string{"None", *key}
	}
	if len(changes) == 0 {
		return nil
	}
	direct := "direct"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "subscription", Key: id, Representation: "Subscription object (" + id + ")",
		Action: 1, Changes: changes, Context: audit, AssociationID: &association, ResolutionPath: &direct, At: at})
}

const subscriptionCreateMemberColumns = `user_id, role, subscription_start_day, subscription_start_month, custom_end_date, subscription_end_day,
 subscription_end_month`

func (t *subscriptionCreateTransaction) oneMember(ctx context.Context, query string, arg any) (*domain.SubscriptionCreateMember, error) {
	var member domain.SubscriptionCreateMember
	err := t.tx.GetContext(ctx, &member, `SELECT `+subscriptionCreateMemberColumns+` FROM bakney_user WHERE `+query, arg)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &member, nil
}

func (t *subscriptionCreateTransaction) MemberByEmail(ctx context.Context, email string) (*domain.SubscriptionCreateMember, error) {
	return t.oneMember(ctx, `email=$1 AND NOT deleted ORDER BY user_id LIMIT 1`, email)
}

func (t *subscriptionCreateTransaction) MemberByUsername(ctx context.Context, username string) (*domain.SubscriptionCreateMember, error) {
	return t.oneMember(ctx, `username=$1 AND NOT deleted`, username)
}

func (t *subscriptionCreateTransaction) Member(ctx context.Context, id string) (*domain.SubscriptionCreateMember, error) {
	return t.oneMember(ctx, `user_id=$1`, id)
}

// InsertNewUser is User.objects.create_user(**validated_data, username=token_hex(10)) with every other field at its model
// default (create entry), then user.username = ...; user.set_password(...); user.save() (the username update entry; the
// password is not audited). The new user has no association: both entries are indexed through the actor.
func (t *subscriptionCreateTransaction) InsertNewUser(ctx context.Context, user domain.SubscriptionCreateNewUser, audit domain.LoginAuditContext) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO bakney_user
 (password,last_login,is_superuser,username,first_name,last_name,email,is_staff,is_active,date_joined,user_id,role,two_fa,
  stripe_on_boarding_completed,enumerate_invoices,online_payments,balance_sheet_year,balance_sheet_start_day,balance_sheet_start_month,
  temporary_invoice_deletion,auto_archive,auto_mark_attendance,full_installments_plan,payment_date_equal_invoice_date,
  starting_number_invoices,auto_paid_payment,tables_settings,deleted,collaborator_role,dark_mode,integration_google_auto_sync,
  subscription_duration_equal_sport_year,subscription_duration,membership_duration,subscription_start_day,subscription_start_month,
  custom_end_date,subscription_end_day,subscription_end_month,membership_starting_number,default_membership_type,
  disable_account_creation,force_account_creation,medical_certificate_notifications,hide_category_name,show_zero_payments,
  imported_from_associami,connected_user_id,collaborator_permissions,phone)
 VALUES ($1,NULL,FALSE,$2,$3,$4,$5,FALSE,TRUE,$6,$7,$8,FALSE,FALSE,FALSE,TRUE,1,1,1,TRUE,FALSE,FALSE,FALSE,FALSE,0,FALSE,'{}'::jsonb,
  FALSE,1,FALSE,FALSE,TRUE,2,2,1,1,FALSE,31,12,0,'',FALSE,FALSE,TRUE,FALSE,FALSE,FALSE,NULL,NULL,$9)`,
		user.PasswordHash, user.TokenUsername, user.FirstName, user.LastName, user.Email, user.Joined, user.ID, user.Role, user.Phone); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "bakney_user", "user_id", user.ID)
	if err != nil {
		return err
	}
	// auditlog.register(User, exclude_fields=['last_login', 'password']); reverse foreign keys with a related_name render as their managers.
	delete(changes, "password")
	delete(changes, "last_login")
	for name, manager := range map[string]string{"requester": "application.SubscriptionTransfer.None", "recipient": "application.SubscriptionTransfer.None",
		"saved_reports": "application.SavedReport.None", "social_auth": "social_django.UserSocialAuth.None"} {
		changes[name] = [2]string{"None", manager}
	}
	representation := "name: " + user.FirstName + " " + user.LastName
	entry := modelAuditEntry{Model: "user", Key: user.ID, Representation: representation, Action: 0, Changes: changes, Context: audit,
		At: user.Joined}
	if err = actorIndex(ctx, t.tx, audit, &entry); err != nil {
		return err
	}
	if err = appendModelAudit(ctx, t.tx, entry); err != nil {
		return err
	}
	if _, err = t.tx.ExecContext(ctx, `UPDATE bakney_user SET username=$2 WHERE user_id=$1`, user.ID, user.Username); err != nil {
		return err
	}
	update := modelAuditEntry{Model: "user", Key: user.ID, Representation: representation, Action: 1,
		Changes: map[string][2]string{"username": {user.TokenUsername, user.Username}}, Context: audit, At: user.Joined}
	if err = actorIndex(ctx, t.tx, audit, &update); err != nil {
		return err
	}
	return appendModelAudit(ctx, t.tx, update)
}
