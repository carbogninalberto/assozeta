package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// InvoiceSyncPrint persists invoice_update and the valid invoice_customers_add.
type InvoiceSyncPrint struct {
	db          *sqlx.DB
	generations *InvoiceGenerations
}

func NewInvoiceSyncPrint(db *sqlx.DB) *InvoiceSyncPrint {
	return &InvoiceSyncPrint{db: db, generations: NewInvoiceGenerations(NewPaymentStates(db))}
}

var (
	_ domain.InvoiceSyncStore         = (*InvoiceSyncPrint)(nil)
	_ domain.CustomerInvoiceSyncStore = (*InvoiceSyncPrint)(nil)
)

// invoiceSyncTransaction extends invoice-generate's transaction (payment
// save defaults, audit and the description reads) with the update's lookups.
type invoiceSyncTransaction struct{ *invoiceGenerateTransaction }

func (r *InvoiceSyncPrint) BeginInvoiceSync(ctx context.Context) (domain.InvoiceSyncTransaction, error) {
	tx, err := r.generations.BeginInvoiceGenerate(ctx)
	if err != nil {
		return nil, err
	}
	return &invoiceSyncTransaction{invoiceGenerateTransaction: tx.(*invoiceGenerateTransaction)}, nil
}

func (t *invoiceSyncTransaction) SyncInvoice(ctx context.Context, id, association string, scope, group *string) (*domain.InvoiceSyncRow, error) {
	var row domain.InvoiceSyncRow
	err := t.tx.GetContext(ctx, &row, `SELECT invoice_id, sport_association_id, number, cancelled, selected_tutor_id, description
 FROM application_invoice WHERE invoice_id=$1 AND sport_association_id=$2 AND NOT archived AND NOT deleted
 AND ($3::uuid IS NULL OR group_id=$3::uuid) AND ($4::uuid IS NULL OR sport_association_id=$4::uuid) ORDER BY invoice_id LIMIT 1`,
		id, association, group, scope)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *invoiceSyncTransaction) SyncAssociate(ctx context.Context, id string, scope, group *string) (bool, error) {
	var found bool
	err := t.tx.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM application_associate WHERE associate_id=$1 AND NOT deleted
 AND ($2::uuid IS NULL OR group_id=$2::uuid) AND ($3::uuid IS NULL OR sport_association_id=$3::uuid))`, id, group, scope)
	return found, err
}

func (t *invoiceSyncTransaction) InvoicePayment(ctx context.Context, invoice string, scope, group *string) (*domain.PaymentStateRow, error) {
	var row domain.PaymentStateRow
	err := t.tx.GetContext(ctx, &row, `SELECT p.payment_id, COALESCE(p.sport_association_id::text, '') AS sport_association_id, p.subject,
 p.archived, p.payment_category_id, c.name AS payment_category_name, p.description, p.meta::text AS meta, p.course::text AS course, p.paid,
 p.expense, p.payment_date, p.invoice_id, p.associate_id, p.supplier_id, p.instructor_id
 FROM application_payment p LEFT JOIN application_paymentcategory c ON c.payment_category_id=p.payment_category_id
 WHERE p.invoice_id=$1 AND NOT p.deleted AND ($2::uuid IS NULL OR p.group_id=$2::uuid) AND ($3::uuid IS NULL OR p.sport_association_id=$3::uuid)
 ORDER BY p.payment_id LIMIT 1 FOR UPDATE OF p`, invoice, group, scope)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *invoiceSyncTransaction) SaveInvoicePayment(ctx context.Context, id string, paymentDate *time.Time, category, description *string) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_payment SET payment_date=$2, payment_category_id=$3, description=$4
 WHERE payment_id=$1`, id, paymentDate, category, description)
	return err
}

// invoiceSyncWrite runs one autocommit step of the views in its own transaction.
func (r *InvoiceSyncPrint) invoiceSyncWrite(ctx context.Context, run func(tx *sqlx.Tx) error) error {
	owned, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return err
	}
	defer owned.Rollback()
	if err = run(owned.tx); err != nil {
		return err
	}
	return owned.Commit()
}

func (r *InvoiceSyncPrint) AppendInvoiceAudit(ctx context.Context, invoice, association string, changes map[string][2]string,
	audit domain.LoginAuditContext, at time.Time) error {
	path := "direct"
	return r.invoiceSyncWrite(ctx, func(tx *sqlx.Tx) error {
		return appendModelAudit(ctx, tx, modelAuditEntry{Model: "invoice", Key: invoice, Representation: "Invoice object (" + invoice + ")",
			Action: 1, Changes: changes, Context: audit, AssociationID: &association, ResolutionPath: &path, At: at})
	})
}

func (r *InvoiceSyncPrint) SaveSyncInvoice(ctx context.Context, save domain.InvoiceSyncSave) error {
	return r.invoiceSyncWrite(ctx, func(tx *sqlx.Tx) error {
		_, err := tx.ExecContext(ctx, `UPDATE application_invoice SET number=$2, cancelled=$3, selected_tutor_id=$4, description=$5
 WHERE invoice_id=$1`, save.ID, save.Number, save.Cancelled, save.SelectedTutorID, save.Description)
		return err
	})
}

func (r *InvoiceSyncPrint) RenderInvoice(ctx context.Context, id string) (*domain.PaymentReadsInvoice, error) {
	var row domain.PaymentReadsInvoice
	if err := r.db.GetContext(ctx, &row, invoiceCoreInvoiceSelect+` WHERE v.invoice_id=$1`, id); err != nil {
		return nil, err
	}
	return &row, nil
}

func (r *InvoiceSyncPrint) CreateCustomerInvoice(ctx context.Context, c domain.NewCustomerInvoice, overrides map[string]string,
	audit domain.LoginAuditContext, at time.Time) error {
	return r.invoiceSyncWrite(ctx, func(tx *sqlx.Tx) error {
		if _, err := tx.ExecContext(ctx, `INSERT INTO application_customerinvoice (customer_invoice_id, prefix, number, fiscal_year,
 assignor_prefix_vat_number, assignor_vat_number, assignor_tax_code, assignor_denomination, assignor_fiscal_regime, assignor_address,
 assignor_postal_code, assignor_city, assignor_province, assignor_country, assignor_contact_email, assignor_contact_phone, assignor_fax,
 transferor_prefix_vat_number, transferor_vat_number, transferor_tax_code, transferor_denomination, transferor_fiscal_regime,
 transferor_address, transferor_postal_code, transferor_city, transferor_province, transferor_country, transferor_contact_email,
 transferor_contact_phone, transferor_fax, e_invoice, country_transmitter_prefix, id_transmitter, transmitting_format, document_type,
 currency, transmitting_date, causal, lines, payment_condition, payment_modality, payment_total_amount, payment_expiry_date, xml, pdf_id,
 json, sport_association_id, payment_id, creation_date, paid, transmitted)
 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,$32,$33,$34,
 $35,$36,$37,$38,$39::jsonb,$40,$41,$42::numeric,$43,$44,$45,$46::jsonb,$47,$48,$49,$50,$51)`,
			c.ID, c.Prefix, c.Number, c.FiscalYear, c.AssignorPrefixVAT, c.AssignorVAT, c.AssignorTaxCode, c.AssignorDenomination,
			c.AssignorFiscalRegime, c.AssignorAddress, c.AssignorPostalCode, c.AssignorCity, c.AssignorProvince, c.AssignorCountry,
			c.AssignorEmail, c.AssignorPhone, c.AssignorFax, c.TransferorPrefixVAT, c.TransferorVAT, c.TransferorTaxCode,
			c.TransferorDenomination, c.TransferorFiscalRegime, c.TransferorAddress, c.TransferorPostalCode, c.TransferorCity,
			c.TransferorProvince, c.TransferorCountry, c.TransferorEmail, c.TransferorPhone, c.TransferorFax, c.EInvoice,
			c.CountryTransmitterPrefix, c.IDTransmitter, c.TransmittingFormat, c.DocumentType, c.Currency, c.TransmittingDate.Format(time.DateOnly),
			c.Causal, c.Lines, c.PaymentCondition, c.PaymentModality, c.PaymentTotalAmount, c.PaymentExpiryDate.Format(time.DateOnly), c.XML,
			c.PDF, c.JSON, c.Association, c.Payment, c.CreationDate, c.Paid, c.Transmitted); err != nil {
			return err
		}
		changes, err := createdModelChanges(ctx, tx, "application_customerinvoice", "customer_invoice_id", c.ID)
		if err != nil {
			return err
		}
		for field, value := range overrides {
			changes[field] = [2]string{"None", value}
		}
		path, association := "direct", c.Association
		return appendModelAudit(ctx, tx, modelAuditEntry{Model: "customerinvoice", Key: c.ID,
			Representation: "CustomerInvoice object (" + c.ID + ")", Action: 0, Changes: changes, Context: audit,
			AssociationID: &association, ResolutionPath: &path, At: at})
	})
}

func (r *InvoiceSyncPrint) SaveCustomerInvoice(ctx context.Context, id, association string, memory map[string]string, xml, json *string,
	audit domain.LoginAuditContext, at time.Time) error {
	return r.invoiceSyncWrite(ctx, func(tx *sqlx.Tx) error {
		stored, _, err := storedModelChanges(ctx, tx, "application_customerinvoice", "customer_invoice_id", id, false, true)
		if err != nil {
			return err
		}
		changes := map[string][2]string{}
		for field, value := range memory {
			old := "None"
			if pair, ok := stored[field]; ok {
				old = pair[0]
			}
			if old != value {
				changes[field] = [2]string{old, value}
			}
		}
		if len(changes) > 0 {
			path := "direct"
			if err = appendModelAudit(ctx, tx, modelAuditEntry{Model: "customerinvoice", Key: id,
				Representation: "CustomerInvoice object (" + id + ")", Action: 1, Changes: changes, Context: audit,
				AssociationID: &association, ResolutionPath: &path, At: at}); err != nil {
				return err
			}
		}
		if xml == nil {
			return nil
		}
		_, err = tx.ExecContext(ctx, `UPDATE application_customerinvoice SET xml=$2, json=$3::jsonb WHERE customer_invoice_id=$1`, id, xml, json)
		return err
	})
}

// PendingCustomerInvoices reads every customer invoice without a PDF (no
// ORDER BY, like the unordered queryset) with invoice-views' row loader.
func (r *InvoiceSyncPrint) PendingCustomerInvoices(ctx context.Context) ([]domain.PendingCustomerInvoice, error) {
	var keys []struct {
		ID          string `db:"customer_invoice_id"`
		Association string `db:"sport_association_id"`
	}
	if err := r.db.SelectContext(ctx, &keys, `SELECT customer_invoice_id::text, sport_association_id::text
 FROM application_customerinvoice WHERE pdf_id IS NULL`); err != nil {
		return nil, err
	}
	views := NewInvoiceViews(r.db)
	pending := make([]domain.PendingCustomerInvoice, 0, len(keys))
	for _, key := range keys {
		row, err := views.CustomerInvoice(ctx, key.ID)
		if err != nil {
			return nil, err
		}
		pending = append(pending, domain.PendingCustomerInvoice{ID: key.ID, Association: key.Association, Row: *row})
	}
	return pending, nil
}

func (r *InvoiceSyncPrint) SaveCustomerInvoiceXML(ctx context.Context, id, association, xml string, audit domain.LoginAuditContext,
	at time.Time) error {
	return r.invoiceSyncWrite(ctx, func(tx *sqlx.Tx) error {
		var stored *string
		if err := tx.GetContext(ctx, &stored, `SELECT xml FROM application_customerinvoice WHERE customer_invoice_id=$1::uuid FOR UPDATE`,
			id); err != nil {
			return err
		}
		old := "None"
		if stored != nil {
			old = *stored
		}
		if old != xml {
			path := "direct"
			if err := appendModelAudit(ctx, tx, modelAuditEntry{Model: "customerinvoice", Key: id,
				Representation: "CustomerInvoice object (" + id + ")", Action: 1, Changes: map[string][2]string{"xml": {old, xml}},
				Context: audit, AssociationID: &association, ResolutionPath: &path, At: at}); err != nil {
				return err
			}
		}
		_, err := tx.ExecContext(ctx, `UPDATE application_customerinvoice SET xml=$2 WHERE customer_invoice_id=$1::uuid`, id, xml)
		return err
	})
}
