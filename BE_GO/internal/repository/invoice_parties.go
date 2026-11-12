package repository

import (
	"context"
	"database/sql"
	"errors"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type InvoiceParties struct{ db *sqlx.DB }

func NewInvoiceParties(db *sqlx.DB) *InvoiceParties { return &InvoiceParties{db: db} }

var _ domain.InvoicePartyStore = (*InvoiceParties)(nil)

const customerInvoiceColumns = `c.customer_invoice_id, c.prefix, c.number, c.fiscal_year, c.assignor_prefix_vat_number,
 c.assignor_vat_number, c.assignor_tax_code, c.assignor_denomination, c.assignor_fiscal_regime, c.assignor_address,
 c.assignor_postal_code, c.assignor_city, c.assignor_province, c.assignor_country, c.assignor_contact_email,
 c.assignor_contact_phone, c.assignor_fax, c.transferor_prefix_vat_number, c.transferor_vat_number, c.transferor_tax_code,
 c.transferor_denomination, c.transferor_fiscal_regime, c.transferor_address, c.transferor_postal_code, c.transferor_city,
 c.transferor_province, c.transferor_country, c.transferor_contact_email, c.transferor_contact_phone, c.transferor_fax,
 c.e_invoice, c.country_transmitter_prefix, c.id_transmitter, c.transmitting_format, c.document_type, c.currency,
 c.transmitting_date, c.causal, c.lines::text AS lines, c.payment_condition, c.payment_modality, c.payment_total_amount::text AS payment_total_amount,
 c.payment_expiry_date, c.xml, c.json::text AS json, c.creation_date, c.paid, c.transmitted, c.pdf_id, c.sport_association_id, c.payment_id,
 d.token::text AS pdf_token
 FROM application_customerinvoice c LEFT JOIN docmanager_document d ON d.document_id=c.pdf_id`

const supplierInvoiceColumns = `i.invoice_supplier_id, i.invoice_identifier, i.amount::text AS amount, i.payment_date, i.expire_date,
 i.sport_association_id, i.payment_id, i.paid, i.notes, i.document_pdf_id, i.supplier_id, d.token::text AS document_token`

const invoicePartyPaymentColumns = `p.payment_id, p.type, p.subject, p.amount::text AS amount, p.creation_date, p.payment_date, p.paid,
 p.expense, p.description, p.payment_category_id, p.meta_payment_categories::text AS meta_payment_categories, p.invoice_id, p.user_id, p.associate_id, p.supplier_id,
 p.instructor_id, p.sport_association_id, p.payment_intent_id, p.archived, p.invoice_fiscal_code, p.meta::text AS meta, p.notes,
 p.custom_accounts_id, p.signature_id, p.deleted, p.group_id, p.course::text AS course, a.account_type, a.name AS account_name,
 c.name AS category_name`

func (r *InvoiceParties) CustomerInvoicesWithoutPDF(ctx context.Context, association string) (bool, error) {
	var exists bool
	err := r.db.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_customerinvoice
 WHERE sport_association_id=$1::uuid AND pdf_id IS NULL)`, association)
	return exists, err
}

func (r *InvoiceParties) OwnerAssociation(ctx context.Context, owner string) (string, error) {
	return paymentCategoryAssociation(ctx, r.db, owner, nil, false)
}

func (r *InvoiceParties) ScopedAssociation(ctx context.Context, owner string, impersonation *string) (*string, error) {
	var id string
	err := r.db.GetContext(ctx, &id, `SELECT sport_association_id FROM application_sportassociation
 WHERE user_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR sport_association_id=$2::uuid) ORDER BY sport_association_id LIMIT 1`,
		owner, impersonation)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &id, nil
}

func (r *InvoiceParties) ActorAssociation(ctx context.Context, actor string) (*string, error) {
	return r.ScopedAssociation(ctx, actor, nil)
}

func (r *InvoiceParties) CustomerInvoices(ctx context.Context, association string) ([]domain.CustomerInvoiceRow, error) {
	rows := []domain.CustomerInvoiceRow{}
	err := r.db.SelectContext(ctx, &rows, `SELECT `+customerInvoiceColumns+` WHERE c.sport_association_id=$1 ORDER BY c.creation_date DESC`,
		association)
	return rows, err
}

func (r *InvoiceParties) CustomerInvoice(ctx context.Context, id, association string) (*domain.CustomerInvoiceRow, error) {
	var row domain.CustomerInvoiceRow
	err := r.db.GetContext(ctx, &row, `SELECT `+customerInvoiceColumns+` WHERE c.customer_invoice_id=$1 AND c.sport_association_id=$2`,
		id, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &row, err
}

// supplierInvoiceWhere is the association's invoices and Django's
// icontains (UPPER(column::text) LIKE UPPER(pattern), the pattern's
// backslash, percent and underscore escaped) over the searched columns.
// Timestamps render as in Django's UTC session.
func supplierInvoiceWhere(query domain.SupplierInvoiceQuery) (string, []any) {
	where := ` FROM application_invoicesuppliers i LEFT JOIN application_supplierandcustomers s ON s.supplier_id=i.supplier_id
 LEFT JOIN docmanager_document d ON d.document_id=i.document_pdf_id WHERE i.sport_association_id=$1`
	args := []any{query.AssociationID}
	like := func(text string) string {
		return "%" + strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`).Replace(text) + "%"
	}
	switch {
	case query.AmountText != nil:
		args = append(args, like(*query.AmountText), *query.AmountExact)
		where += ` AND (UPPER(i.amount::text) LIKE UPPER($2) OR i.amount=$3::numeric)`
	case query.Text != nil:
		args = append(args, like(*query.Text))
		timestamp := func(column string) string {
			return `UPPER(((` + column + ` AT TIME ZONE 'UTC')::text || '+00')) LIKE UPPER($2)`
		}
		where += ` AND (UPPER(i.notes::text) LIKE UPPER($2) OR UPPER(i.invoice_identifier::text) LIKE UPPER($2) OR ` +
			timestamp("i.payment_date") + ` OR ` + timestamp("i.expire_date") + ` OR UPPER(s.name::text) LIKE UPPER($2))`
	}
	return where, args
}

func (r *InvoiceParties) CountSupplierInvoices(ctx context.Context, query domain.SupplierInvoiceQuery) (int64, error) {
	where, args := supplierInvoiceWhere(query)
	var count int64
	err := r.db.GetContext(ctx, &count, `SELECT count(*)`+where, args...)
	return count, err
}

// SupplierInvoices orders only by a column the service resolved from the
// model's fields; the name is never caller text.
func (r *InvoiceParties) SupplierInvoices(ctx context.Context, query domain.SupplierInvoiceQuery, limit, offset int64,
	group *string) ([]domain.SupplierInvoiceRow, error) {
	where, args := supplierInvoiceWhere(query)
	statement := `SELECT ` + supplierInvoiceColumns + where + ` ORDER BY i.` + query.OrderColumn
	if query.Descending {
		statement += ` DESC`
	}
	statement += ` LIMIT ` + strconv.FormatInt(limit, 10) + ` OFFSET ` + strconv.FormatInt(offset, 10)
	rows := []domain.SupplierInvoiceRow{}
	if err := r.db.SelectContext(ctx, &rows, statement, args...); err != nil {
		return nil, err
	}
	for index := range rows {
		if err := r.related(ctx, &rows[index], group); err != nil {
			return nil, err
		}
	}
	return rows, nil
}

func (r *InvoiceParties) SupplierInvoice(ctx context.Context, id, association string) (*domain.SupplierInvoiceRow, error) {
	var row domain.SupplierInvoiceRow
	err := r.db.GetContext(ctx, &row, `SELECT `+supplierInvoiceColumns+` FROM application_invoicesuppliers i
 LEFT JOIN docmanager_document d ON d.document_id=i.document_pdf_id WHERE i.invoice_supplier_id=$1 AND i.sport_association_id=$2`,
		id, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &row, err
}

// related loads the invoice's payment and supplier through the base managers
// (forward FK access), with what PaymentSerializer renders.
func (r *InvoiceParties) related(ctx context.Context, row *domain.SupplierInvoiceRow, group *string) error {
	var err error
	if row.SupplierID != nil {
		if row.Supplier, err = r.supplier(ctx, *row.SupplierID); err != nil {
			return err
		}
	}
	if row.PaymentID != nil {
		if row.Payment, _, err = r.Payment(ctx, *row.PaymentID, group); err != nil {
			return err
		}
		if row.Payment == nil {
			return errors.New("supplier invoice payment row missing")
		}
		if payment := row.Payment; payment.AssociateID != nil || payment.InstructorID != nil || payment.InvoiceID != nil ||
			payment.Subject != 0 && payment.Subject != 3 {
			if row.Render, err = r.render(ctx, *row.PaymentID, group); err != nil {
				return err
			}
		}
	}
	return nil
}

func (r *InvoiceParties) supplier(ctx context.Context, id string) (*domain.Supplier, error) {
	var supplier domain.Supplier
	err := r.db.GetContext(ctx, &supplier, `SELECT `+supplierColumns+` FROM application_supplierandcustomers WHERE supplier_id=$1`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &supplier, err
}

func (r *InvoiceParties) Payment(ctx context.Context, id string, group *string) (*domain.InvoicePartyPayment, bool, error) {
	var payment domain.InvoicePartyPayment
	err := r.db.GetContext(ctx, &payment, `SELECT `+invoicePartyPaymentColumns+` FROM application_payment p
 LEFT JOIN application_customaccounts a ON a.custom_account_id=p.custom_accounts_id
 LEFT JOIN application_paymentcategory c ON c.payment_category_id=p.payment_category_id WHERE p.payment_id=$1`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, err
	}
	visible := !payment.Deleted && (group == nil || payment.GroupID != nil && *payment.GroupID == *group)
	if payment.UserID != nil {
		var user domain.InvoicePartyUser
		if err = r.db.GetContext(ctx, &user, `SELECT first_name, last_name, email, phone, username, role FROM bakney_user WHERE user_id=$1`,
			*payment.UserID); err != nil {
			return nil, false, err
		}
		payment.User = &user
	}
	if payment.SupplierID != nil {
		if payment.Supplier, err = r.supplier(ctx, *payment.SupplierID); err != nil {
			return nil, false, err
		}
	}
	payment.Attachments = []domain.InvoicePartyDocument{}
	if err = r.db.SelectContext(ctx, &payment.Attachments, `SELECT d.document_id, d.creation_date, d.filepath, d.filename,
 d.file_size_bytes, d.token::text AS token FROM application_payment_attachments l JOIN docmanager_document d ON d.document_id=l.document_id
 WHERE l.payment_id=$1 ORDER BY l.id`, id); err != nil {
		return nil, false, err
	}
	if payment.Subject == 0 && payment.AssociateID == nil {
		// Subscription.objects.filter(associate=None, archived=False).order_by('creation_date').first()
		var subscription string
		err = r.db.GetContext(ctx, &subscription, `SELECT subscription_id FROM application_subscription
 WHERE associate_id IS NULL AND NOT archived AND ($1::uuid IS NULL OR group_id=$1::uuid) ORDER BY creation_date LIMIT 1`, group)
		if err == nil {
			payment.Subscription = &subscription
		} else if !errors.Is(err, sql.ErrNoRows) {
			return nil, false, err
		}
	}
	return &payment, visible, nil
}

var invoicePartyTables = map[string]bool{"application_customerinvoice.customer_invoice_id": true, "docmanager_document.document_id": true,
	"application_invoicesuppliers.invoice_supplier_id": true, "application_sportassociation.sport_association_id": true}

func (r *InvoiceParties) Exists(ctx context.Context, table, key, id string) (bool, error) {
	if !invoicePartyTables[table+"."+key] {
		return false, errors.New("invoice party lookup table not allowed")
	}
	var exists bool
	err := r.db.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM `+table+` WHERE `+key+`=$1)`, id)
	return exists, err
}

func (r *InvoiceParties) PaymentAssociation(ctx context.Context, id string, group *string) (bool, *string, error) {
	var association *string
	err := r.db.GetContext(ctx, &association, `SELECT sport_association_id FROM application_payment
 WHERE payment_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR group_id=$2::uuid)`, id, group)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil, nil
	}
	return err == nil, association, err
}

func (r *InvoiceParties) CustomerInvoiceTaken(ctx context.Context, prefix string, number, fiscalYear int64, association string) (bool, error) {
	var exists bool
	err := r.db.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_customerinvoice
 WHERE prefix=$1 AND number=$2 AND fiscal_year=$3 AND sport_association_id=$4)`, prefix, number, fiscalYear, association)
	return exists, err
}

// CustomerNumberTaken is filter(number=..., fiscal_year=...).exists(): None is IS NULL.
func (r *InvoiceParties) CustomerNumberTaken(ctx context.Context, number, fiscalYear *int64) (bool, error) {
	var exists bool
	err := r.db.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_customerinvoice
 WHERE number IS NOT DISTINCT FROM $1::integer AND fiscal_year IS NOT DISTINCT FROM $2::integer)`, number, fiscalYear)
	return exists, err
}

// BankAccount is the view's CustomAccounts lookup: the association's default
// bank account (unordered first(): ordered by the primary key) or the named one.
func (r *InvoiceParties) BankAccount(ctx context.Context, association string, id *string) (*string, error) {
	var account string
	var err error
	if id == nil {
		err = r.db.GetContext(ctx, &account, `SELECT custom_account_id FROM application_customaccounts
 WHERE sport_association_id=$1 AND NOT editable AND enabled AND account_type=2 ORDER BY custom_account_id LIMIT 1`, association)
	} else {
		err = r.db.GetContext(ctx, &account, `SELECT custom_account_id FROM application_customaccounts
 WHERE sport_association_id=$1 AND custom_account_id=$2`, association, *id)
	}
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &account, nil
}

func (r *InvoiceParties) LiveSupplier(ctx context.Context, id, association string, group *string) (*domain.Supplier, error) {
	var supplier domain.Supplier
	err := r.db.GetContext(ctx, &supplier, `SELECT `+supplierColumns+` FROM application_supplierandcustomers
 WHERE supplier_id=$1 AND sport_association_id=$2 AND NOT deleted AND ($3::uuid IS NULL OR group_id=$3::uuid)
 ORDER BY supplier_id LIMIT 1`, id, association, group)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &supplier, err
}

func (r *InvoiceParties) AssociationExists(ctx context.Context, id string) (bool, error) {
	var exists bool
	err := r.db.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_sportassociation WHERE sport_association_id=$1)`, id)
	return exists, err
}

func (r *InvoiceParties) GroupAssociation(ctx context.Context, id string) (*string, bool, error) {
	var association *string
	err := r.db.GetContext(ctx, &association, `SELECT sport_association_id FROM application_group WHERE group_id=$1`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, false, nil
	}
	return association, err == nil, err
}

func (r *InvoiceParties) SupplierIDTaken(ctx context.Context, id string, group *string, exclude *string) (bool, error) {
	var taken bool
	err := r.db.GetContext(ctx, &taken, `SELECT EXISTS(SELECT 1 FROM application_supplierandcustomers
 WHERE supplier_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR group_id=$2::uuid) AND ($3::uuid IS NULL OR supplier_id<>$3::uuid))`, id, group, exclude)
	return taken, err
}

// SupplierPaymentCategory prefers the association's live "Pagamento
// Fornitore" category, then a shared one (iexact; unordered first()).
func (r *InvoiceParties) SupplierPaymentCategory(ctx context.Context, association string) (*string, error) {
	for _, owner := range []*string{&association, nil} {
		var id string
		err := r.db.GetContext(ctx, &id, `SELECT payment_category_id FROM application_paymentcategory
 WHERE UPPER(name::text)=UPPER('Pagamento Fornitore') AND NOT deleted AND sport_association_id IS NOT DISTINCT FROM $1::uuid
 ORDER BY payment_category_id LIMIT 1`, owner)
		if err == nil {
			return &id, nil
		}
		if !errors.Is(err, sql.ErrNoRows) {
			return nil, err
		}
	}
	return nil, nil
}

// DefaultPaymentCategory is Payment.save()'s default category lookup (PaymentCategory.default_for).
func (r *InvoiceParties) DefaultPaymentCategory(ctx context.Context, association *string) (*string, *string, error) {
	var row struct {
		ID   string `db:"payment_category_id"`
		Name string `db:"name"`
	}
	err := r.db.GetContext(ctx, &row, secCategoryDefaultSQL, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil, nil
	}
	if err != nil {
		return nil, nil, err
	}
	return &row.ID, &row.Name, nil
}

// courses is the shared payment loader on one read transaction.
func (r *InvoiceParties) courses(ctx context.Context, run func(t *courseSubscriptionTransaction) error) error {
	owned, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return err
	}
	defer owned.Rollback()
	return run(&courseSubscriptionTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: owned}})
}

func (r *InvoiceParties) PaymentLinkedName(ctx context.Context, payment string) (domain.PaymentLinkedName, error) {
	var linked domain.PaymentLinkedName
	err := r.courses(ctx, func(t *courseSubscriptionTransaction) error {
		var err error
		linked, err = (&paymentArchiveTransaction{paymentCategoryTransaction: t.paymentCategoryTransaction}).PaymentLinkedName(ctx, payment)
		return err
	})
	return linked, err
}

func (r *InvoiceParties) PaymentCreatedChanges(ctx context.Context, payment string) (map[string][2]string, error) {
	var changes map[string][2]string
	err := r.courses(ctx, func(t *courseSubscriptionTransaction) error {
		var err error
		changes, err = createdModelChanges(ctx, t.tx, "application_payment", "payment_id", payment)
		return err
	})
	return changes, err
}

// render loads everything PaymentSerializer nests for one payment.
func (r *InvoiceParties) render(ctx context.Context, id string, group *string) (*domain.PaymentStateRender, error) {
	var render domain.PaymentStateRender
	err := r.courses(ctx, func(t *courseSubscriptionTransaction) error {
		payment, err := t.payment(ctx, id, group)
		if err != nil {
			return err
		}
		render.Payment = payment
		render.Instructor, render.Invoice, err = paymentNestedRows(ctx, t.tx, payment.InvoicePartyPayment)
		return err
	})
	if errors.Is(err, domain.ErrCourseSubscriptionServer) {
		// A lookup of get_subscription_id that raises (several rows, a missing subscription).
		return nil, domain.ErrInvoicePartyServer
	}
	return &render, err
}

// write runs one autocommit step of the view in its own transaction.
func (r *InvoiceParties) write(ctx context.Context, run func(tx *sqlx.Tx) error) error {
	owned, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return err
	}
	defer owned.Rollback()
	if err = run(owned.tx); err != nil {
		return err
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	return owned.Commit()
}

func invoicePartyEntry(entry domain.InvoicePartyAudit, audit domain.LoginAuditContext, at time.Time) modelAuditEntry {
	representations := map[string]string{"payment": "Payment", "invoicesuppliers": "InvoiceSuppliers", "customerinvoice": "CustomerInvoice"}
	return modelAuditEntry{Model: entry.Model, Key: entry.Key, Representation: representations[entry.Model] + " object (" + entry.Key + ")",
		Action: entry.Action, Changes: entry.Changes, Context: audit, AssociationID: entry.AssociationID, ResolutionPath: entry.ResolutionPath,
		At: at}
}

func (r *InvoiceParties) AppendAudit(ctx context.Context, entry domain.InvoicePartyAudit, audit domain.LoginAuditContext, at time.Time) error {
	return r.write(ctx, func(tx *sqlx.Tx) error { return appendModelAudit(ctx, tx, invoicePartyEntry(entry, audit, at)) })
}

// CreateSupplierPayment is PaymentEntrySerializer.save() (create, audited
// from the stored row) then payment.save() with the user, association and
// supplier (audited as their change).
func (r *InvoiceParties) CreateSupplierPayment(ctx context.Context, payment domain.NewSupplierPayment, created, saved domain.InvoicePartyAudit,
	owner, association, supplier string, audit domain.LoginAuditContext, at time.Time) error {
	return r.write(ctx, func(tx *sqlx.Tx) error {
		if _, err := tx.ExecContext(ctx, `INSERT INTO application_payment (payment_id,type,subject,amount,creation_date,payment_date,paid,
 expense,description,payment_category_id,archived,custom_accounts_id,deleted,group_id)
 VALUES ($1,'default',0,$2::numeric,$3,$4,$5,true,$6,$7,false,$8,false,$9)`, payment.ID, payment.Amount, payment.CreationDate,
			payment.PaymentDate, payment.Paid, payment.Description, payment.CategoryID, payment.AccountID, payment.GroupID); err != nil {
			return err
		}
		changes, err := createdModelChanges(ctx, tx, "application_payment", "payment_id", payment.ID)
		if err != nil {
			return err
		}
		created.Changes = changes
		if err = appendModelAudit(ctx, tx, invoicePartyEntry(created, audit, at)); err != nil {
			return err
		}
		if err = appendModelAudit(ctx, tx, invoicePartyEntry(saved, audit, at)); err != nil {
			return err
		}
		_, err = tx.ExecContext(ctx, `UPDATE application_payment SET user_id=$2, sport_association_id=$3, supplier_id=$4 WHERE payment_id=$1`,
			payment.ID, owner, association, supplier)
		return err
	})
}

// CreateSupplierInvoice is InvoiceSuppliers.objects.create(**validated) then
// the save with the payment and supplier.
func (r *InvoiceParties) CreateSupplierInvoice(ctx context.Context, invoice domain.NewSupplierInvoice, created, linked domain.InvoicePartyAudit,
	payment, supplier string, audit domain.LoginAuditContext, at time.Time) error {
	return r.write(ctx, func(tx *sqlx.Tx) error {
		if _, err := tx.ExecContext(ctx, `INSERT INTO application_invoicesuppliers (invoice_supplier_id,invoice_identifier,amount,
 payment_date,expire_date,sport_association_id,paid,notes,document_pdf_id,payment_id) VALUES ($1,$2,$3::numeric,$4,$5,$6,$7,$8,$9,$10)`,
			invoice.ID, invoice.Identifier, invoice.Amount, invoice.PaymentDate, invoice.ExpireDate, invoice.AssociationID, invoice.Paid,
			invoice.Notes, invoice.DocumentID, invoice.PaymentID); err != nil {
			return err
		}
		// The key constraint is deferred: a payment_id without its row fails here, as the INSERT does in autocommit.
		if _, err := tx.ExecContext(ctx, `SET CONSTRAINTS ALL IMMEDIATE`); err != nil {
			return err
		}
		changes, err := createdModelChanges(ctx, tx, "application_invoicesuppliers", "invoice_supplier_id", invoice.ID)
		if err != nil {
			return err
		}
		if invoice.PaymentText != nil {
			// The entry carries str() of the payment_id as sent.
			changes["payment"] = [2]string{"None", *invoice.PaymentText}
		}
		created.Changes = changes
		if err = appendModelAudit(ctx, tx, invoicePartyEntry(created, audit, at)); err != nil {
			return err
		}
		if err = appendModelAudit(ctx, tx, invoicePartyEntry(linked, audit, at)); err != nil {
			return err
		}
		_, err = tx.ExecContext(ctx, `UPDATE application_invoicesuppliers SET payment_id=$2, supplier_id=$3 WHERE invoice_supplier_id=$1`,
			invoice.ID, payment, supplier)
		return err
	})
}

func (r *InvoiceParties) UpdateCustomerInvoice(ctx context.Context, id string, paid, transmitted bool) error {
	return r.write(ctx, func(tx *sqlx.Tx) error {
		_, err := tx.ExecContext(ctx, `UPDATE application_customerinvoice SET paid=$2, transmitted=$3 WHERE customer_invoice_id=$1`,
			id, paid, transmitted)
		return err
	})
}

func (r *InvoiceParties) UpdateSupplierInvoice(ctx context.Context, row domain.SupplierInvoiceRow) error {
	return r.write(ctx, func(tx *sqlx.Tx) error {
		_, err := tx.ExecContext(ctx, `UPDATE application_invoicesuppliers SET invoice_identifier=$2, amount=$3::numeric, payment_date=$4,
 expire_date=$5, payment_id=$6, paid=$7, notes=$8, document_pdf_id=$9 WHERE invoice_supplier_id=$1`, row.ID, row.Identifier, row.Amount,
			row.PaymentDate, row.ExpireDate, row.PaymentID, row.Paid, row.Notes, row.DocumentID)
		return err
	})
}

func (r *InvoiceParties) UpdatePaymentPaid(ctx context.Context, id string, paid bool, at time.Time, category, description *string) error {
	return r.write(ctx, func(tx *sqlx.Tx) error {
		_, err := tx.ExecContext(ctx, `UPDATE application_payment SET paid=$2, payment_date=$3, payment_category_id=$4, description=$5
 WHERE payment_id=$1`, id, paid, at, category, description)
		return err
	})
}
