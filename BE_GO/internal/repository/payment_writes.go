package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type PaymentWrites struct{ states *PaymentStates }

func NewPaymentWrites(states *PaymentStates) *PaymentWrites { return &PaymentWrites{states: states} }

var _ domain.PaymentWritesStore = (*PaymentWrites)(nil)

// paymentWritesTransaction extends the payment state transaction (payment
// save defaults, rendering and the association lookups).
type paymentWritesTransaction struct{ *paymentStateTransaction }

func (r *PaymentWrites) BeginPaymentWrites(ctx context.Context) (domain.PaymentWritesTransaction, error) {
	tx, err := r.states.BeginPaymentState(ctx)
	if err != nil {
		return nil, err
	}
	return &paymentWritesTransaction{paymentStateTransaction: tx.(*paymentStateTransaction)}, nil
}

func (t *paymentWritesTransaction) optional(ctx context.Context, query string, args ...any) (*string, error) {
	var value *string
	err := t.tx.GetContext(ctx, &value, query, args...)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return value, err
}

func (t *paymentWritesTransaction) exists(ctx context.Context, query string, args ...any) (bool, error) {
	var found bool
	err := t.tx.GetContext(ctx, &found, `SELECT EXISTS(`+query+`)`, args...)
	return found, err
}

func (t *paymentWritesTransaction) TenantAssociation(ctx context.Context, user string) (*string, error) {
	return t.optional(ctx, `SELECT sport_association_id::text FROM application_sportassociation WHERE user_id=$1 AND NOT deleted
 ORDER BY sport_association_id LIMIT 1`, user)
}

func (t *paymentWritesTransaction) AutoPaid(ctx context.Context, user string) (bool, error) {
	var paid bool
	err := t.tx.GetContext(ctx, &paid, `SELECT auto_paid_payment FROM bakney_user WHERE user_id=$1`, user)
	return paid, err
}

const paymentWriteColumns = `payment_id, type, subject, amount::text AS amount, creation_date, payment_date, paid, expense, description,
 payment_category_id, meta_payment_categories::text AS meta_payment_categories, invoice_id, user_id, associate_id, supplier_id, instructor_id,
 sport_association_id, payment_intent_id, archived, invoice_fiscal_code, meta::text AS meta, notes, custom_accounts_id, signature_id,
 deleted, group_id, course::text AS course`

func (t *paymentWritesTransaction) LockPayment(ctx context.Context, id string, group, scope *string) (*domain.PaymentWriteRow, error) {
	var row domain.PaymentWriteRow
	err := t.tx.GetContext(ctx, &row, `SELECT `+paymentWriteColumns+` FROM application_payment
 WHERE payment_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR group_id=$2::uuid) AND ($3::uuid IS NULL OR sport_association_id=$3::uuid)
 FOR UPDATE`, id, group, scope)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

var paymentWritesRelated = map[string]string{
	"payment_category": `SELECT sport_association_id::text FROM application_paymentcategory WHERE payment_category_id=$1`,
	"custom_accounts":  `SELECT sport_association_id::text FROM application_customaccounts WHERE custom_account_id=$1`,
}

func (t *paymentWritesTransaction) RelatedOwner(ctx context.Context, table, id string) (bool, *string, error) {
	query, ok := paymentWritesRelated[table]
	if !ok {
		return false, nil, domain.ErrPaymentWritesServer
	}
	var owner *string
	err := t.tx.GetContext(ctx, &owner, query, id)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil, nil
	}
	return err == nil, owner, err
}

func (t *paymentWritesTransaction) AssociationExists(ctx context.Context, id string) (bool, error) {
	return t.exists(ctx, `SELECT 1 FROM application_sportassociation WHERE sport_association_id=$1`, id)
}

func (t *paymentWritesTransaction) LiveAssociation(ctx context.Context, id string) (bool, error) {
	return t.exists(ctx, `SELECT 1 FROM application_sportassociation WHERE sport_association_id=$1 AND NOT deleted`, id)
}

func (t *paymentWritesTransaction) CategoryName(ctx context.Context, id string) (*string, error) {
	return t.optional(ctx, `SELECT name FROM application_paymentcategory WHERE payment_category_id=$1`, id)
}

// DefaultBankAccount is Payment.get_default_bank_account (first() by pk).
func (t *paymentWritesTransaction) DefaultBankAccount(ctx context.Context, association string) (*string, error) {
	return t.optional(ctx, `SELECT custom_account_id::text FROM application_customaccounts
 WHERE sport_association_id=$1 AND NOT editable AND enabled AND account_type=2 ORDER BY custom_account_id LIMIT 1`, association)
}

func (t *paymentWritesTransaction) GroupExists(ctx context.Context, id string) (bool, error) {
	return t.exists(ctx, `SELECT 1 FROM application_group WHERE group_id=$1::uuid`, id)
}

func (t *paymentWritesTransaction) InstructorExists(ctx context.Context, id string) (bool, error) {
	return t.exists(ctx, `SELECT 1 FROM application_instructor WHERE instructor_id=$1`, id)
}

func (t *paymentWritesTransaction) SupplierExists(ctx context.Context, id string, group *string) (bool, error) {
	return t.exists(ctx, `SELECT 1 FROM application_supplierandcustomers WHERE supplier_id=$1 AND NOT deleted
 AND ($2::uuid IS NULL OR group_id=$2::uuid)`, id, group)
}

func (t *paymentWritesTransaction) SubscriptionAssociate(ctx context.Context, id string, group, scope *string) (bool, *string, error) {
	var associate *string
	err := t.tx.GetContext(ctx, &associate, `SELECT associate_id::text FROM application_subscription WHERE subscription_id=$1
 AND ($2::uuid IS NULL OR group_id=$2::uuid) AND ($3::uuid IS NULL OR sport_association_id=$3::uuid)`, id, group, scope)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil, nil
	}
	return err == nil, associate, err
}

func (t *paymentWritesTransaction) AssociateExists(ctx context.Context, id string, group, scope *string) (bool, error) {
	return t.exists(ctx, `SELECT 1 FROM application_associate WHERE associate_id=$1 AND NOT deleted
 AND ($2::uuid IS NULL OR group_id=$2::uuid) AND ($3::uuid IS NULL OR sport_association_id=$3::uuid)`, id, group, scope)
}

func paymentWriteArgs(row domain.PaymentWriteRow) []any {
	return []any{row.ID, row.Type, row.Subject, row.Amount, row.CreationDate, row.PaymentDate, row.Paid, row.Expense, row.Description,
		row.CategoryID, row.MetaCategories, row.InvoiceID, row.UserID, row.AssociateID, row.SupplierID, row.InstructorID, row.AssociationID,
		row.PaymentIntentID, row.Archived, row.InvoiceFiscalCode, row.Meta, row.Notes, row.AccountID, row.SignatureID, row.Deleted, row.GroupID,
		row.Course}
}

func (t *paymentWritesTransaction) InsertPaymentRow(ctx context.Context, row domain.PaymentWriteRow) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_payment (payment_id, type, subject, amount, creation_date, payment_date, paid,
 expense, description, payment_category_id, meta_payment_categories, invoice_id, user_id, associate_id, supplier_id, instructor_id,
 sport_association_id, payment_intent_id, archived, invoice_fiscal_code, meta, notes, custom_accounts_id, signature_id, deleted, group_id,
 course) VALUES ($1,$2,$3,$4::numeric,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21::jsonb,$22,$23,$24,$25,$26::uuid,
 $27::jsonb)`, paymentWriteArgs(row)...)
	return err
}

// SavePaymentRow is Model.save()'s full-row UPDATE.
func (t *paymentWritesTransaction) SavePaymentRow(ctx context.Context, row domain.PaymentWriteRow) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_payment SET type=$2, subject=$3, amount=$4::numeric, creation_date=$5, payment_date=$6,
 paid=$7, expense=$8, description=$9, payment_category_id=$10, meta_payment_categories=$11::jsonb, invoice_id=$12, user_id=$13,
 associate_id=$14, supplier_id=$15, instructor_id=$16, sport_association_id=$17, payment_intent_id=$18, archived=$19,
 invoice_fiscal_code=$20, meta=$21::jsonb, notes=$22, custom_accounts_id=$23, signature_id=$24, deleted=$25, group_id=$26::uuid,
 course=$27::jsonb WHERE payment_id=$1`, paymentWriteArgs(row)...)
	return err
}

// paymentAuditFields are Payment's concrete fields (auditlog names).
var paymentAuditFields = []string{"payment_id", "type", "subject", "amount", "creation_date", "payment_date", "paid", "expense", "description",
	"payment_category", "meta_payment_categories", "invoice", "user", "associate", "supplier", "instructor", "sport_association",
	"payment_intent_id", "archived", "invoice_fiscal_code", "meta", "notes", "custom_accounts", "signature", "deleted", "group", "course"}

func (t *paymentWritesTransaction) PaymentAuditValues(ctx context.Context, id string) (map[string]string, error) {
	changes, _, err := storedModelChanges(ctx, t.tx, "application_payment", "payment_id", id, true, false)
	if err != nil {
		return nil, err
	}
	values := make(map[string]string, len(paymentAuditFields))
	for _, name := range paymentAuditFields {
		values[name] = "None"
		if pair, ok := changes[name]; ok {
			values[name] = pair[1]
		}
	}
	return values, nil
}

func (t *paymentWritesTransaction) AppendPaymentWritesAudit(ctx context.Context, entry domain.PaymentWritesAuditEntry) error {
	var path *string
	if entry.Association != nil {
		path = &entry.Path
	}
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "payment", Key: entry.PaymentID,
		Representation: "Payment object (" + entry.PaymentID + ")", Action: entry.Action, Changes: entry.Changes, Context: entry.Context,
		AssociationID: entry.Association, ResolutionPath: path, At: entry.At})
}

func (t *paymentWritesTransaction) BulkAssociates(ctx context.Context, ids []string, association string, group, scope *string) ([]string, error) {
	found := []string{}
	if len(ids) == 0 {
		return found, nil
	}
	err := t.tx.SelectContext(ctx, &found, `SELECT associate_id::text FROM application_associate WHERE associate_id=ANY($1::uuid[])
 AND sport_association_id=$2 AND NOT deleted AND ($3::uuid IS NULL OR group_id=$3::uuid) AND ($4::uuid IS NULL OR sport_association_id=$4::uuid)`,
		ids, association, group, scope)
	return found, err
}

func (t *paymentWritesTransaction) CategoryExists(ctx context.Context, id string) (bool, error) {
	return t.exists(ctx, `SELECT 1 FROM application_paymentcategory WHERE payment_category_id=$1`, id)
}

func (t *paymentWritesTransaction) AssociationAccount(ctx context.Context, id, association string) (*string, error) {
	return t.optional(ctx, `SELECT custom_account_id::text FROM application_customaccounts WHERE custom_account_id=$1 AND sport_association_id=$2
 ORDER BY custom_account_id LIMIT 1`, id, association)
}

// InsertBulkPayments is bulk_create: one INSERT per row, no signals, no audit.
func (t *paymentWritesTransaction) InsertBulkPayments(ctx context.Context, rows []domain.PaymentBulkRow) error {
	for _, row := range rows {
		if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_payment (payment_id, type, subject, amount, creation_date, payment_date,
 paid, expense, description, payment_category_id, meta_payment_categories, user_id, associate_id, sport_association_id, archived, notes,
 custom_accounts_id, deleted) VALUES ($1,$2,$3,$4::numeric,$5,$6,$7,$8,$9,$10,$11::jsonb,$12,$13,$14,false,$15,$16,false)`,
			row.ID, row.Type, row.Subject, row.Amount, row.CreationDate, row.PaymentDate, row.Paid, row.Expense, row.Description, row.CategoryID,
			row.Meta, row.UserID, row.AssociateID, row.AssociationID, row.Notes, row.AccountID); err != nil {
			return err
		}
	}
	return nil
}
