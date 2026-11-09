package repository

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strconv"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// MiscExports serves subscription_list_export and invoice_list_export.
// Queries keep the views' filters and orderings.
type MiscExports struct{ db *sqlx.DB }

func NewMiscExports(db *sqlx.DB) *MiscExports { return &MiscExports{db: db} }

var _ domain.MiscExportStore = (*MiscExports)(nil)

type miscExportTransaction struct{ *ownedTransaction }

func (r *MiscExports) BeginMiscExports(ctx context.Context) (domain.MiscExportTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &miscExportTransaction{ownedTransaction: tx}, nil
}

func (t *miscExportTransaction) ScopedAssociation(ctx context.Context, user string, scope *string) (*domain.PaymentExportAssociation, error) {
	id, err := paymentCategoryAssociation(ctx, t.tx, user, scope, false)
	if errors.Is(err, domain.ErrAssociationNotFound) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var association domain.PaymentExportAssociation
	err = t.tx.GetContext(ctx, &association, `SELECT sport_association_id, denomination FROM application_sportassociation
 WHERE sport_association_id=$1`, id)
	return &association, err
}

const miscExportPersonColumns = `%[1]s.first_name, %[1]s.last_name, %[1]s.sex, %[1]s.tax_code, %[1]s.born_date, %[1]s.born_city,
 %[1]s.address_city, %[1]s.address, %[1]s.address_cap, %[1]s.email, %[1]s.phone_label, %[1]s.phone, %[1]s.phone_2_label, %[1]s.phone_2,
 %[1]s.phone_3_label, %[1]s.phone_3, %[1]s.phone_4_label, %[1]s.phone_4, COALESCE(%[1]s.is_minor, false) AS is_minor`

func miscExportPersonSelect(alias string) string { return fmt.Sprintf(miscExportPersonColumns, alias) }

// Subscriptions: scoped_queryset(Subscription.objects) (group-aware, soft-deleted
// rows kept) .filter(sport_association, archived=False) with the associate and
// medical certificate joined as select_related does, ordered by -creation_date.
func (t *miscExportTransaction) Subscriptions(ctx context.Context, association string, group, scope *string) ([]domain.MiscExportSubscription, error) {
	rows := []domain.MiscExportSubscription{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT s.subscription_id, s.status_flag, s.creation_date, s.start_date, s.end_date,
 s.subscription_number, s.subscription_type, s.signature_storage_key, s.signature_url, s.medical_id IS NOT NULL AS has_medical,
 m.expiration_date AS medical_expiration, s.associate_id, a.sport_association_id AS associate_association, `+
		miscExportPersonSelect("a")+`
 FROM application_subscription s LEFT JOIN application_associate a ON a.associate_id=s.associate_id
 LEFT JOIN application_medicalcertificate m ON m.medical_id=s.medical_id
 WHERE s.sport_association_id=$1::uuid AND NOT s.archived AND ($2::uuid IS NULL OR s.group_id=$2::uuid)
 AND ($3::uuid IS NULL OR s.sport_association_id=$3::uuid) ORDER BY s.creation_date DESC`, association, group, scope)
	return rows, err
}

// Tags is prefetch_related('tags'): the through join without ordering.
func (t *miscExportTransaction) Tags(ctx context.Context, subscriptions []string) (map[string][]string, error) {
	result := map[string][]string{}
	if len(subscriptions) == 0 {
		return result, nil
	}
	rows := []struct {
		Subscription string `db:"subscription_id"`
		Name         string `db:"tag_name"`
	}{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT st.subscription_id, t.tag_name FROM application_tags t
 INNER JOIN application_subscription_tags st ON t.tag_id=st.tags_id WHERE st.subscription_id = ANY($1::uuid[])`, uuidArray(subscriptions))
	for _, row := range rows {
		result[row.Subscription] = append(result[row.Subscription], row.Name)
	}
	return result, err
}

// Relations are the associates' tutor relations (by primary key) with their
// tutors through the base manager.
func (t *miscExportTransaction) Relations(ctx context.Context, associates []string) ([]domain.MiscExportRelation, error) {
	rows := []domain.MiscExportRelation{}
	if len(associates) == 0 {
		return rows, nil
	}
	err := t.tx.SelectContext(ctx, &rows, `SELECT r.id, r.associate_id AS relation_associate_id, r.is_primary, `+miscExportPersonSelect("p")+`
 FROM application_associatetutorrelation r JOIN application_associate p ON p.associate_id=r.tutor_id
 WHERE r.associate_id = ANY($1::uuid[]) ORDER BY r.id`, uuidArray(associates))
	return rows, err
}

// PromoteRelation is get_main_tutor's tutor_relation.save() with is_primary=True
// and its auditlog update entry.
func (t *miscExportTransaction) PromoteRelation(ctx context.Context, promotion domain.MiscExportPromotion) error {
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_associatetutorrelation SET is_primary=true WHERE id=$1`, promotion.RelationID); err != nil {
		return err
	}
	key := strconv.FormatInt(promotion.RelationID, 10)
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "associatetutorrelation", Key: key,
		Representation: "AssociateTutorRelation object (" + key + ")", Action: 1,
		Changes: map[string][2]string{"is_primary": {"False", "True"}}, Context: promotion.Context,
		AssociationID: promotion.AssociationID, ResolutionPath: promotion.Path, At: promotion.At})
}

func (t *miscExportTransaction) ActiveAssociationOf(ctx context.Context, user string) (*string, error) {
	var id string
	err := t.tx.GetContext(ctx, &id, `SELECT sport_association_id FROM application_sportassociation
 WHERE user_id=$1 AND NOT deleted ORDER BY sport_association_id LIMIT 1`, user)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &id, err
}

// Invoices: scoped_queryset(Invoice.objects) (live, group-aware)
// .filter(sport_association, archived) ordered by creation_date.
func (t *miscExportTransaction) Invoices(ctx context.Context, association string, archived bool, group, scope *string) ([]domain.MiscExportInvoice, error) {
	rows := []domain.MiscExportInvoice{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT i.invoice_id, i.creation_date, i.membership_fee::text AS membership_fee,
 i.activity_fee::text AS activity_fee, i.number, i.document_pdf_id FROM application_invoice i
 WHERE NOT i.deleted AND ($3::uuid IS NULL OR i.group_id=$3::uuid) AND ($4::uuid IS NULL OR i.sport_association_id=$4::uuid)
 AND i.sport_association_id=$1::uuid AND i.archived=$2 ORDER BY i.creation_date`, association, archived, group, scope)
	return rows, err
}

// FirstPayments is invoice.payment_set.first() over the prefetch queryset
// (scoped, live, group-aware Payment.objects): Payment has no default
// ordering, so first() orders by primary key.
func (t *miscExportTransaction) FirstPayments(ctx context.Context, invoices []string, group, scope *string) (map[string]domain.MiscExportPayment, error) {
	result := map[string]domain.MiscExportPayment{}
	if len(invoices) == 0 {
		return result, nil
	}
	rows := []domain.MiscExportPayment{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT DISTINCT ON (p.invoice_id) p.invoice_id, p.type, p.associate_id IS NOT NULL AS has_associate,
 a.first_name, a.last_name FROM application_payment p LEFT JOIN application_associate a ON a.associate_id=p.associate_id
 WHERE p.invoice_id = ANY($1::uuid[]) AND NOT p.deleted AND ($2::uuid IS NULL OR p.group_id=$2::uuid)
 AND ($3::uuid IS NULL OR p.sport_association_id=$3::uuid) ORDER BY p.invoice_id, p.payment_id`, uuidArray(invoices), group, scope)
	for _, row := range rows {
		result[row.InvoiceID] = row
	}
	return result, err
}
