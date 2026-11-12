package repository

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

var _ domain.PersonaSubsWriteStore = (*PersonaSubscriptions)(nil)

type personaSubsWriteTransaction struct{ *ownedTransaction }

func (r *PersonaSubscriptions) BeginPersonaSubsWrite(ctx context.Context) (domain.PersonaSubsWriteTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &personaSubsWriteTransaction{ownedTransaction: tx}, nil
}

// personaSubsUserAssociation is user_resolver: the user's first live association by key.
const personaSubsUserAssociation = `(SELECT sa.sport_association_id FROM application_sportassociation sa WHERE sa.user_id=%s AND NOT sa.deleted
 ORDER BY sa.sport_association_id LIMIT 1)`

// personaSubsRelated: each related field's default manager and the owner its
// audit resolver gives (SportAssociation.original_objects, the live
// group-aware SupplierAndCustomers and Payment managers, User.original_objects;
// Document has no resolver).
var personaSubsRelated = map[string]string{
	"sport_association": `SELECT sport_association_id FROM application_sportassociation WHERE sport_association_id=$1::uuid`,
	"supplier": `SELECT sport_association_id FROM application_supplierandcustomers WHERE supplier_id=$1::uuid AND NOT deleted
 AND ($2::uuid IS NULL OR group_id=$2::uuid)`,
	"instructor":   `SELECT ` + fmt.Sprintf(personaSubsUserAssociation, "i.user_id") + ` FROM application_instructor i WHERE i.instructor_id=$1::uuid`,
	"user":         `SELECT ` + fmt.Sprintf(personaSubsUserAssociation, "u.user_id") + ` FROM bakney_user u WHERE u.user_id=$1::uuid`,
	"document_pdf": `SELECT NULL::uuid FROM docmanager_document WHERE document_id=$1::uuid`,
	"payment": `SELECT sport_association_id FROM application_payment WHERE payment_id=$1::uuid AND NOT deleted
 AND ($2::uuid IS NULL OR group_id=$2::uuid)`,
	"group": `SELECT sport_association_id FROM application_group WHERE group_id=$1::uuid`,
	"tags":  `SELECT sport_association_id FROM application_tags WHERE tag_id=$1::uuid`,
}

func (t *personaSubsWriteTransaction) Related(ctx context.Context, field, id string, group *string) (bool, *string, error) {
	query, ok := personaSubsRelated[field]
	if !ok {
		return false, nil, fmt.Errorf("unknown related field %q", field)
	}
	args := []any{id}
	if field == "supplier" || field == "payment" {
		args = append(args, group)
	}
	var owner *string
	err := t.tx.GetContext(ctx, &owner, query, args...)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil, nil
	}
	return err == nil, owner, err
}

func (t *personaSubsWriteTransaction) TenantAssociation(ctx context.Context, user string) (*string, error) {
	var owner *string
	err := t.tx.GetContext(ctx, &owner, `SELECT `+fmt.Sprintf(personaSubsUserAssociation, "$1::uuid"), user)
	return owner, err
}

func (t *personaSubsWriteTransaction) Subscription(ctx context.Context, id string) (*domain.PersonaSubscriptionRow, error) {
	var row domain.PersonaSubscriptionRow
	err := t.tx.GetContext(ctx, &row, `SELECT subscription_id, sport_association_id, associate_id, supplier_id, instructor_id, custom_data,
 additional_fields, user_id, type, role, acceptance_date, resignation_date, medical_id, status_flag, creation_date, start_date, end_date,
 signature_url, signature_storage_key, document_pdf_id, payment_id, archived, draft, trial, meta, subscription_number, subscription_type,
 group_id, deleted, competitive, notes FROM application_subscription WHERE subscription_id=$1::uuid FOR UPDATE`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *personaSubsWriteTransaction) DateRule(ctx context.Context, association string) (domain.SubscriptionDateRule, error) {
	var settings struct {
		StartDay   int  `db:"subscription_start_day"`
		StartMonth int  `db:"subscription_start_month"`
		CustomEnd  bool `db:"custom_end_date"`
		EndDay     int  `db:"subscription_end_day"`
		EndMonth   int  `db:"subscription_end_month"`
		Duration   int  `db:"subscription_duration"`
	}
	err := t.tx.GetContext(ctx, &settings, `SELECT u.subscription_start_day, u.subscription_start_month, u.custom_end_date,
 u.subscription_end_day, u.subscription_end_month, u.subscription_duration FROM application_sportassociation a
 JOIN bakney_user u ON u.user_id=a.user_id WHERE a.sport_association_id=$1::uuid`, association)
	return domain.SubscriptionDateRule{Duration: settings.Duration, Season: domain.CalendarRule{StartDay: settings.StartDay,
		StartMonth: settings.StartMonth, CustomEnd: settings.CustomEnd, EndDay: settings.EndDay, EndMonth: settings.EndMonth}}, err
}

func (t *personaSubsWriteTransaction) TagIDs(ctx context.Context, subscription string) ([]string, error) {
	tags := []string{}
	err := t.tx.SelectContext(ctx, &tags, `SELECT tags_id::text FROM application_subscription_tags WHERE subscription_id=$1::uuid ORDER BY id`,
		subscription)
	return tags, err
}

func personaSubsDate(value *time.Time) *string {
	if value == nil {
		return nil
	}
	text := value.Format("2006-01-02")
	return &text
}

func personaSubsJSON(value *[]byte) *string {
	if value == nil {
		return nil
	}
	text := string(*value)
	return &text
}

// SaveSubscription is Subscription.save() after the serializer's setattr:
// auditlog's pre_save entry (indexed directly to the stored association),
// then the UPDATE of every column.
func (t *personaSubsWriteTransaction) SaveSubscription(ctx context.Context, row domain.PersonaSubscriptionRow, changes map[string][2]string,
	audit domain.CourseSubscriptionAudit) error {
	if err := t.AuditSubscription(ctx, row.ID, changes, audit); err != nil {
		return err
	}
	custom, additional, meta := personaSubsJSONColumns(row)
	_, err := t.tx.ExecContext(ctx, `UPDATE application_subscription SET sport_association_id=$2::uuid, associate_id=$3::uuid,
 supplier_id=$4::uuid, instructor_id=$5::uuid, custom_data=$6::jsonb, additional_fields=$7::jsonb, user_id=$8::uuid, type=$9, role=$10,
 acceptance_date=$11::date, resignation_date=$12::date, medical_id=$13::uuid, status_flag=$14, creation_date=$15, start_date=$16::date,
 end_date=$17::date, signature_url=$18, signature_storage_key=$19, document_pdf_id=$20::uuid, payment_id=$21::uuid, deleted=$22,
 archived=$23, draft=$24, trial=$25, meta=$26::jsonb, subscription_number=$27, subscription_type=$28, group_id=$29::uuid,
 competitive=$30, notes=$31 WHERE subscription_id=$1::uuid`, row.ID, row.AssociationID, row.AssociateID, row.SupplierID,
		row.InstructorID, personaSubsJSON(custom), personaSubsJSON(additional), row.UserID, row.Type, row.Role, personaSubsDate(row.AcceptanceDate),
		personaSubsDate(row.ResignationDate), row.MedicalID, row.StatusFlag, row.CreationDate, personaSubsDate(row.StartDate),
		personaSubsDate(row.EndDate), row.SignatureURL, row.SignatureStorageKey, row.DocumentID, row.PaymentID, row.Deleted, row.Archived,
		row.Draft, row.Trial, personaSubsJSON(meta), row.SubscriptionNumber, row.SubscriptionType, row.GroupID, row.Competitive, row.Notes)
	return err
}

func personaSubsJSONColumns(row domain.PersonaSubscriptionRow) (custom, additional, meta *[]byte) {
	if row.CustomData != nil {
		value := []byte(*row.CustomData)
		custom = &value
	}
	if row.AdditionalFields != nil {
		value := []byte(*row.AdditionalFields)
		additional = &value
	}
	if row.Meta != nil {
		value := []byte(*row.Meta)
		meta = &value
	}
	return custom, additional, meta
}

// AuditSubscription is auditlog's pre_save update entry, indexed directly to
// the stored association (none without changes).
func (t *personaSubsWriteTransaction) AuditSubscription(ctx context.Context, id string, changes map[string][2]string,
	audit domain.CourseSubscriptionAudit) error {
	if len(changes) == 0 {
		return nil
	}
	var association string
	if err := t.tx.GetContext(ctx, &association, `SELECT sport_association_id FROM application_subscription WHERE subscription_id=$1::uuid`,
		id); err != nil {
		return err
	}
	return appendModelAudit(ctx, t.tx, courseSubscriptionEntry("subscription", "Subscription", id, "", 1, changes, &association, "direct", audit))
}

// RemoveTags is the M2M remove() of tags.set().
func (t *personaSubsWriteTransaction) RemoveTags(ctx context.Context, subscription string, tags []string) error {
	_, err := t.tx.ExecContext(ctx, `DELETE FROM application_subscription_tags WHERE subscription_id=$1::uuid AND tags_id::text = ANY($2)`,
		subscription, tags)
	return err
}

// AddTag is the M2M add() of tags.set() for one new link.
func (t *personaSubsWriteTransaction) AddTag(ctx context.Context, subscription, tag string) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_subscription_tags (subscription_id, tags_id) VALUES ($1::uuid, $2::uuid)`,
		subscription, tag)
	return err
}

// RouterSubscriptionByID renders a subscription by key, any state (the
// serializer's instance after the update), with the group-aware renewal check.
func (r *PersonaSubscriptions) RouterSubscriptionByID(ctx context.Context, id string, group *string, today time.Time) (*domain.PersonaSubscriptionRow, error) {
	var row domain.PersonaSubscriptionRow
	err := r.db.GetContext(ctx, &row, `SELECT s.subscription_id, s.sport_association_id, s.associate_id, s.supplier_id,
 s.instructor_id, s.custom_data, s.additional_fields, s.user_id, s.type, s.role, s.acceptance_date, s.resignation_date, s.medical_id,
 m.expiration_date AS medical_expiration, s.status_flag, s.creation_date, s.start_date, s.end_date, s.signature_url,
 s.signature_storage_key, s.document_pdf_id, s.payment_id, s.archived, s.draft, s.trial, s.meta, s.subscription_number,
 s.subscription_type, s.group_id, s.deleted, s.competitive, s.notes,
 (SELECT string_agg(t.tags_id::text, ',') FROM application_subscription_tags t WHERE t.subscription_id=s.subscription_id) AS tags,
 (s.start_date IS NOT NULL AND s.end_date IS NOT NULL AND NOT (s.start_date <= $3::date AND $3::date <= s.end_date) AND EXISTS(
   SELECT 1 FROM application_subscription f LEFT JOIN application_associate fa ON fa.associate_id=f.associate_id
   WHERE f.sport_association_id=s.sport_association_id AND f.start_date >= s.end_date AND NOT f.archived
   AND ($2::uuid IS NULL OR f.group_id=$2::uuid)
   AND (CASE WHEN a.tax_code IS NULL THEN fa.tax_code IS NULL ELSE UPPER(fa.tax_code::text)=UPPER(a.tax_code::text) END))) AS future_renewal
 FROM application_subscription s
 LEFT JOIN application_medicalcertificate m ON m.medical_id=s.medical_id
 LEFT JOIN application_associate a ON a.associate_id=s.associate_id
 WHERE s.subscription_id=$1::uuid`, id, group, today.Format("2006-01-02"))
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}
