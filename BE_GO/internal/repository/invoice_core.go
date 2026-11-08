package repository

import (
	"context"
	"database/sql"
	"errors"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type InvoiceCore struct {
	db       *sqlx.DB
	personas *Personas
}

func NewInvoiceCore(db *sqlx.DB) *InvoiceCore { return &InvoiceCore{db: db, personas: NewPersonas(db)} }

var _ domain.InvoiceCoreStore = (*InvoiceCore)(nil)

func (r *InvoiceCore) UserAssociation(ctx context.Context, user string) (*string, error) {
	ids := []string{}
	if err := r.db.SelectContext(ctx, &ids, `SELECT sport_association_id FROM application_sportassociation WHERE user_id=$1`, user); err != nil {
		return nil, err
	}
	if len(ids) == 0 {
		return nil, nil
	}
	return &ids[0], nil
}

func (r *InvoiceCore) ScopedAssociation(ctx context.Context, user string, scope *string) (string, error) {
	return paymentCategoryAssociation(ctx, r.db, user, scope, false)
}

func (r *InvoiceCore) BalanceStart(ctx context.Context, user string) (int64, int64, error) {
	var row struct {
		Day   int64 `db:"balance_sheet_start_day"`
		Month int64 `db:"balance_sheet_start_month"`
	}
	err := r.db.GetContext(ctx, &row, `SELECT balance_sheet_start_day, balance_sheet_start_month FROM bakney_user WHERE user_id=$1`, user)
	return row.Day, row.Month, err
}

// invoiceCoreInvoiceSelect is InvoiceSerializer's columns (v) with the PDF token and the attached document keys.
const invoiceCoreInvoiceSelect = `SELECT v.invoice_id, v.document_pdf_id, d.token::text AS document_token,
 v.sport_association_id, v.creation_date, v.membership_fee::text AS membership_fee, v.activity_fee::text AS activity_fee,
 v.meta_payment_categories::text AS meta_payment_categories, v.description, v.number, v.archived, v.cancelled, v.selected_tutor_id,
 v.meta::text AS meta, v.imported_from_associami, v.group_id, v.deleted,
 COALESCE((SELECT json_agg(l.document_id ORDER BY l.id) FROM application_invoice_attached_documents l WHERE l.invoice_id=v.invoice_id),
  '[]'::json)::text AS attached_documents
 FROM application_invoice v LEFT JOIN docmanager_document d ON d.document_id=v.document_pdf_id`

// invoiceCoreLive is Invoice.objects (live, X-Group-Id-aware) plus scoped_queryset's association.
const invoiceCoreLive = ` NOT v.deleted AND ($2::uuid IS NULL OR v.group_id=$2::uuid) AND ($3::uuid IS NULL OR v.sport_association_id=$3::uuid)`

func (r *InvoiceCore) Invoice(ctx context.Context, id string, group, scope *string) (*domain.PaymentReadsInvoice, error) {
	rows := []domain.PaymentReadsInvoice{}
	if err := r.db.SelectContext(ctx, &rows, invoiceCoreInvoiceSelect+` WHERE v.invoice_id=$1 AND`+invoiceCoreLive, id, group, scope); err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return nil, nil
	}
	return &rows[0], nil
}

func (r *InvoiceCore) Invoices(ctx context.Context, ids []string) (map[string]domain.PaymentReadsInvoice, error) {
	rows := []domain.PaymentReadsInvoice{}
	result := map[string]domain.PaymentReadsInvoice{}
	if len(ids) == 0 {
		return result, nil
	}
	if err := r.db.SelectContext(ctx, &rows, invoiceCoreInvoiceSelect+` WHERE v.invoice_id = ANY($1::uuid[])`, ids); err != nil {
		return nil, err
	}
	for _, row := range rows {
		result[row.ID] = row
	}
	return result, nil
}

func (r *InvoiceCore) ArchivedInvoices(ctx context.Context, association string, group, scope *string) ([]domain.PaymentReadsInvoice, error) {
	rows := []domain.PaymentReadsInvoice{}
	err := r.db.SelectContext(ctx, &rows, invoiceCoreInvoiceSelect+` WHERE v.sport_association_id=$1 AND v.archived AND`+invoiceCoreLive+
		` ORDER BY v.creation_date DESC`, association, group, scope)
	return rows, err
}

// invoiceCoreSearchFields are get_controlled_searchable_fields(Invoice): the
// concrete non-relation fields, selected_tutor's listed fields and the
// payment's associate, supplier and instructor fields.
var invoiceCoreSearchFields = func() []paymentReadsField {
	fields := []paymentReadsField{}
	for _, name := range []string{"invoice_id", "creation_date", "membership_fee", "activity_fee", "meta_payment_categories", "description",
		"number", "archived", "cancelled", "meta", "imported_from_associami", "deleted"} {
		timestamp := name == "creation_date"
		fields = append(fields, paymentReadsField{column: "i." + name, timestamp: timestamp, date: timestamp})
	}
	fields = append(fields, paymentReadsPerson("st")...)
	fields = append(fields, paymentReadsField{column: "sa.first_name", permuted: true}, paymentReadsField{column: "sa.last_name", permuted: true},
		paymentReadsField{column: "ss.name", permuted: true}, paymentReadsField{column: "ss.tax_code"}, paymentReadsField{column: "ss.vat_number"},
		paymentReadsField{column: "si.first_name", permuted: true}, paymentReadsField{column: "si.last_name", permuted: true})
	return fields
}()

// invoiceCoreSearchJoins are smart_search's LEFT JOINs, one row per linked payment (any state).
const invoiceCoreSearchJoins = ` FROM (SELECT 1) d
 LEFT JOIN application_associate st ON st.associate_id=i.selected_tutor_id
 LEFT JOIN application_payment sp ON sp.invoice_id=i.invoice_id
 LEFT JOIN application_associate sa ON sa.associate_id=sp.associate_id
 LEFT JOIN application_supplierandcustomers ss ON ss.supplier_id=sp.supplier_id
 LEFT JOIN application_instructor si ON si.instructor_id=sp.instructor_id`

func invoiceCoreSmartCondition(search *domain.PaymentReadsSmartSearch, args *personaArgs) string {
	words := make([]string, len(search.Words))
	for i, word := range search.Words {
		parameter := args.add(word)
		terms := []string{}
		for _, field := range invoiceCoreSearchFields {
			terms = append(terms, personaLike(paymentReadsText(field.column, field.timestamp), parameter))
		}
		for _, variation := range search.Dates[i] {
			variationParameter := args.add(variation)
			for _, field := range invoiceCoreSearchFields {
				if field.date {
					terms = append(terms, personaLike(paymentReadsText(field.column, field.timestamp), variationParameter))
				}
			}
		}
		words[i] = "(" + strings.Join(terms, " OR ") + ")"
	}
	condition := "(" + strings.Join(words, " AND ") + ")"
	if len(search.Permutations) > 0 {
		terms := []string{}
		for _, permutation := range search.Permutations {
			parameter := args.add(permutation)
			for _, field := range invoiceCoreSearchFields {
				if field.permuted {
					terms = append(terms, personaLike(paymentReadsText(field.column, field.timestamp), parameter))
				}
			}
		}
		condition = "(" + condition + " OR " + strings.Join(terms, " OR ") + ")"
	}
	return condition
}

// invoiceCoreQuery is FROM ... WHERE ... of invoice_list's queryset. Without
// smart_search the payment joins multiply the rows (no distinct()); with it
// (even a term without words) the queryset is distinct, so the joins become
// existence conditions.
func invoiceCoreQuery(filter domain.InvoiceCoreFilter, args *personaArgs) string {
	distinct := filter.Smart != nil
	from := ` FROM application_invoice i`
	group := args.add(filter.Group)
	where := []string{"NOT i.deleted", "(" + group + "::uuid IS NULL OR i.group_id=" + group + "::uuid)"}
	if filter.Scope != nil {
		where = append(where, "i.sport_association_id="+args.add(*filter.Scope)+"::uuid")
	}
	where = append(where, "i.sport_association_id="+args.add(filter.AssociationID)+"::uuid", "i.archived="+args.add(filter.Archived))
	if distinct {
		where = append(where, "(EXISTS(SELECT 1 FROM application_payment p1 WHERE p1.invoice_id=i.invoice_id) OR i.meta IS NOT NULL)")
	} else {
		from += ` LEFT JOIN application_payment p1 ON p1.invoice_id=i.invoice_id`
		where = append(where, "(p1.payment_id IS NOT NULL OR i.meta IS NOT NULL)")
	}
	if filter.Year != nil {
		start, end := args.add(filter.Year.From), args.add(filter.Year.To)
		condition := "(p2.payment_date < " + start + " OR p2.payment_date > " + end + ")"
		if filter.Year.Within {
			condition = "p2.payment_date >= " + start + " AND p2.payment_date <= " + end
		}
		if distinct {
			where = append(where, "EXISTS(SELECT 1 FROM application_payment p2 WHERE p2.invoice_id=i.invoice_id AND "+condition+")")
		} else {
			from += ` JOIN application_payment p2 ON p2.invoice_id=i.invoice_id`
			where = append(where, condition)
		}
	}
	if filter.AmountText != nil {
		exact := args.add(*filter.AmountExact)
		where = append(where, "("+personaLike("i.number::text", args.add(paymentReadsContains(*filter.AmountText)))+" OR i.membership_fee="+
			exact+"::numeric OR i.activity_fee="+exact+"::numeric)")
	}
	if filter.Smart != nil && len(filter.Smart.Words) > 0 {
		where = append(where, "EXISTS(SELECT 1"+invoiceCoreSearchJoins+" WHERE "+invoiceCoreSmartCondition(filter.Smart, args)+")")
	}
	return from + "\n WHERE " + strings.Join(where, " AND ")
}

// invoiceCoreColumns are the Invoice columns the service may order by.
var invoiceCoreColumns = map[string]bool{"invoice_id": true, "document_pdf_id": true, "sport_association_id": true, "creation_date": true,
	"membership_fee": true, "activity_fee": true, "meta_payment_categories": true, "description": true, "number": true, "archived": true,
	"cancelled": true, "selected_tutor_id": true, "meta": true, "imported_from_associami": true, "group_id": true, "deleted": true}

func (r *InvoiceCore) CountInvoices(ctx context.Context, filter domain.InvoiceCoreFilter) (int64, error) {
	args := personaArgs{}
	body := invoiceCoreQuery(filter, &args)
	var count int64
	err := r.db.GetContext(ctx, &count, "SELECT COUNT(*)"+body, args...)
	return count, err
}

func (r *InvoiceCore) InvoiceRows(ctx context.Context, filter domain.InvoiceCoreFilter, limit, offset int64) ([]string, error) {
	args := personaArgs{}
	statement := "SELECT i.invoice_id" + invoiceCoreQuery(filter, &args)
	terms := []string{"i.number DESC", "i.creation_date DESC"}
	if filter.Order != nil {
		terms = make([]string, len(filter.Order))
		for i, order := range filter.Order {
			if !invoiceCoreColumns[order.Column] {
				return nil, domain.ErrInvoiceCoreNotPorted
			}
			terms[i] = "i." + order.Column
			if order.Descending {
				terms[i] += " DESC"
			}
		}
	}
	statement += " ORDER BY " + strings.Join(terms, ", ") + " LIMIT " + args.add(limit) + " OFFSET " + args.add(offset)
	ids := []string{}
	err := r.db.SelectContext(ctx, &ids, statement, args...)
	return ids, err
}

func (r *InvoiceCore) FirstPayments(ctx context.Context, invoices []string, group, scope *string, byKey bool) (map[string]string, error) {
	result := map[string]string{}
	if len(invoices) == 0 {
		return result, nil
	}
	order := ""
	if byKey {
		order = " ORDER BY payment_id"
	}
	rows := []domain.InvoiceCorePayment{}
	if err := r.db.SelectContext(ctx, &rows, `SELECT invoice_id, payment_id FROM application_payment
 WHERE invoice_id = ANY($1::uuid[]) AND NOT deleted AND ($2::uuid IS NULL OR group_id=$2::uuid)
 AND ($3::uuid IS NULL OR sport_association_id=$3::uuid)`+order, invoices, group, scope); err != nil {
		return nil, err
	}
	for _, row := range rows {
		if _, seen := result[row.InvoiceID]; !seen {
			result[row.InvoiceID] = row.PaymentID
		}
	}
	return result, nil
}

// withPayments runs load inside a read transaction that can use the course
// subscriptions' PaymentSerializer loader.
func (r *InvoiceCore) withPayments(ctx context.Context, load func(*courseSubscriptionTransaction) error) error {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted, ReadOnly: true})
	if err != nil {
		return err
	}
	defer tx.Rollback()
	return load(&courseSubscriptionTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}})
}

// ListPayments loads what PaymentInvoiceSerializer renders for each payment.
func (r *InvoiceCore) ListPayments(ctx context.Context, ids []string, group *string) (map[string]domain.InvoiceCoreListPayment, error) {
	result := map[string]domain.InvoiceCoreListPayment{}
	if len(ids) == 0 {
		return result, nil
	}
	err := r.withPayments(ctx, func(t *courseSubscriptionTransaction) error {
		for _, id := range ids {
			row, err := t.payment(ctx, id, group)
			if err != nil {
				return err
			}
			result[id] = domain.InvoiceCoreListPayment{Payment: row.InvoicePartyPayment}
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	associates := []domain.PaymentReadsAssociate{}
	if err := r.db.SelectContext(ctx, &associates, `SELECT a.associate_id, a.first_name, a.last_name, a.is_minor FROM application_associate a
 WHERE a.associate_id IN (SELECT associate_id FROM application_payment WHERE payment_id = ANY($1::uuid[]))`, ids); err != nil {
		return nil, err
	}
	associateIDs := make([]string, len(associates))
	for i, row := range associates {
		associateIDs[i] = row.ID
	}
	relations, err := r.personas.Relations(ctx, associateIDs)
	if err != nil {
		return nil, err
	}
	tutorIDs := make([]string, len(relations))
	for i, relation := range relations {
		tutorIDs[i] = relation.ID
	}
	second, err := r.personas.Tutors(ctx, tutorIDs, group)
	if err != nil {
		return nil, err
	}
	secondIDs := make([]string, len(second))
	for i, relation := range second {
		secondIDs[i] = relation.ID
	}
	third, err := r.personas.Tutors(ctx, secondIDs, group)
	if err != nil {
		return nil, err
	}
	for id, item := range result {
		if item.Payment.AssociateID == nil {
			continue
		}
		for _, associate := range associates {
			if associate.ID != *item.Payment.AssociateID {
				continue
			}
			found := associate
			item.Associate, item.Second, item.Third = &found, second, third
			for _, relation := range relations {
				if relation.AssociateID == associate.ID {
					item.Relations = append(item.Relations, relation)
				}
			}
		}
		result[id] = item
	}
	return result, nil
}

// ArchivedPayments loads what PaymentSerializer renders for each payment.
func (r *InvoiceCore) ArchivedPayments(ctx context.Context, ids []string, group *string) (map[string]domain.InvoiceCoreArchivedPayment, error) {
	result := map[string]domain.InvoiceCoreArchivedPayment{}
	if len(ids) == 0 {
		return result, nil
	}
	err := r.withPayments(ctx, func(t *courseSubscriptionTransaction) error {
		for _, id := range ids {
			row, err := t.payment(ctx, id, group)
			if err != nil {
				return err
			}
			result[id] = domain.InvoiceCoreArchivedPayment{CourseSubscriptionPayment: row}
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	instructors := []domain.InstructorRow{}
	if err := r.db.SelectContext(ctx, &instructors, instructorColumns+` WHERE i.instructor_id IN (SELECT instructor_id FROM application_payment
 WHERE payment_id = ANY($1::uuid[]))`, ids); err != nil {
		return nil, err
	}
	invoices := []domain.PaymentReadsInvoice{}
	if err := r.db.SelectContext(ctx, &invoices, invoiceCoreInvoiceSelect+` WHERE v.invoice_id IN (SELECT invoice_id FROM application_payment
 WHERE payment_id = ANY($1::uuid[]))`, ids); err != nil {
		return nil, err
	}
	for id, item := range result {
		payment := item.InvoicePartyPayment
		for i := range instructors {
			if payment.InstructorID != nil && instructors[i].ID == *payment.InstructorID {
				item.Instructor = &instructors[i]
			}
		}
		for i := range invoices {
			if payment.InvoiceID != nil && invoices[i].ID == *payment.InvoiceID {
				item.Invoice = &invoices[i]
			}
		}
		result[id] = item
	}
	return result, nil
}

func (r *InvoiceCore) BeginInvoiceCore(ctx context.Context) (domain.InvoiceCoreTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &invoiceCoreTransaction{paymentArchiveTransaction: &paymentArchiveTransaction{
		paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}}, nil
}

type invoiceCoreTransaction struct{ *paymentArchiveTransaction }

func (t *invoiceCoreTransaction) ScopedAssociation(ctx context.Context, user string, scope *string) (string, error) {
	return paymentCategoryAssociation(ctx, t.tx, user, scope, false)
}

func (t *invoiceCoreTransaction) MatchingInvoices(ctx context.Context, association string, ids []string, group, scope *string) ([]string, error) {
	matched := []string{}
	if len(ids) == 0 {
		return matched, nil
	}
	err := t.tx.SelectContext(ctx, &matched, `SELECT v.invoice_id FROM application_invoice v WHERE v.invoice_id = ANY($1::uuid[]) AND`+invoiceCoreLive+
		` AND v.sport_association_id=$4 ORDER BY v.invoice_id FOR UPDATE`, ids, group, scope, association)
	return matched, err
}

func (t *invoiceCoreTransaction) ArchiveInvoices(ctx context.Context, ids []string) error {
	if len(ids) == 0 {
		return nil
	}
	_, err := t.tx.ExecContext(ctx, `UPDATE application_invoice SET archived=TRUE WHERE invoice_id = ANY($1::uuid[])`, ids)
	return err
}

func (t *invoiceCoreTransaction) PaymentToUnlink(ctx context.Context, invoice string, group, scope *string) (*domain.InvoiceCorePaymentSave, error) {
	var row domain.InvoiceCorePaymentSave
	err := t.tx.GetContext(ctx, &row, `SELECT p.payment_id, COALESCE(p.sport_association_id::text, '') AS sport_association_id,
 p.sport_association_id AS association, p.subject, p.archived, p.paid, p.payment_category_id, c.name AS payment_category_name, p.description,
 p.meta::text AS meta, p.course::text AS course
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

func (t *invoiceCoreTransaction) UnlinkPayment(ctx context.Context, row domain.InvoiceCorePaymentSave) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_payment SET invoice_id=NULL, paid=FALSE, payment_category_id=$2, description=$3
 WHERE payment_id=$1`, row.ID, row.CategoryID, row.Description)
	return err
}

// actorIndex is the index resolver's last fallback: the actor's live association.
func (t *invoiceCoreTransaction) actorIndex(ctx context.Context, audit domain.LoginAuditContext) (*string, *string, error) {
	if audit.ActorID == nil {
		return nil, nil, nil
	}
	id, err := paymentCategoryAssociation(ctx, t.tx, *audit.ActorID, nil, false)
	if errors.Is(err, domain.ErrAssociationNotFound) {
		return nil, nil, nil
	}
	if err != nil {
		return nil, nil, err
	}
	path := "actor.sport_association"
	return &id, &path, nil
}

func (t *invoiceCoreTransaction) AppendPaymentSave(ctx context.Context, row domain.InvoiceCorePaymentSave, changes map[string][2]string,
	audit domain.InvoiceCoreDeleteContext) error {
	association, path := row.Association, new(string)
	*path = "direct"
	if association == nil {
		var err error
		if association, path, err = t.actorIndex(ctx, audit.Audit); err != nil {
			return err
		}
	}
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "payment", Key: row.ID, Representation: "Payment object (" + row.ID + ")",
		Action: 1, Changes: changes, Context: audit.Audit, AssociationID: association, ResolutionPath: path, At: audit.At})
}

// DeleteInvoices follows the collector: attached-document links and the
// payments' invoice (any payment state) without entries, then the invoice
// rows and the invoices, each model in descending key order, with their
// auditlog delete entries.
func (t *invoiceCoreTransaction) DeleteInvoices(ctx context.Context, ids []string, audit domain.InvoiceCoreDeleteContext) error {
	if len(ids) == 0 {
		return nil
	}
	if _, err := t.tx.ExecContext(ctx, `DELETE FROM application_invoice_attached_documents WHERE invoice_id = ANY($1::uuid[])`, ids); err != nil {
		return err
	}
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_payment SET invoice_id=NULL WHERE invoice_id = ANY($1::uuid[])`, ids); err != nil {
		return err
	}
	rows := []string{}
	if err := t.tx.SelectContext(ctx, &rows, `SELECT invoice_row_id FROM application_invoicerows WHERE invoice_id = ANY($1::uuid[])
 ORDER BY invoice_row_id DESC FOR UPDATE`, ids); err != nil {
		return err
	}
	// The rows are gone and their changes carry no association: the actor's.
	rowAssociation, rowPath, err := t.actorIndex(ctx, audit.Audit)
	if err != nil {
		return err
	}
	for _, id := range rows {
		changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_invoicerows", "invoice_row_id", id)
		if err != nil {
			return err
		}
		if err = appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "invoicerows", Key: id, Representation: "InvoiceRows object (" + id + ")",
			Action: 2, Changes: changes, Context: audit.Audit, AssociationID: rowAssociation, ResolutionPath: rowPath, At: audit.At}); err != nil {
			return err
		}
	}
	ordered := []string{}
	if err := t.tx.SelectContext(ctx, &ordered, `SELECT invoice_id FROM application_invoice WHERE invoice_id = ANY($1::uuid[]) ORDER BY invoice_id DESC`,
		ids); err != nil {
		return err
	}
	for _, id := range ordered {
		changes, stored, err := deleteModelWithAudit(ctx, t.tx, "application_invoice", "invoice_id", id)
		if err != nil {
			return err
		}
		association, path := (*string)(nil), (*string)(nil)
		if value, ok := stored["sport_association_id"].(string); ok {
			var live bool
			if err := t.tx.GetContext(ctx, &live, `SELECT EXISTS(SELECT 1 FROM application_sportassociation WHERE sport_association_id=$1 AND NOT deleted)`,
				value); err != nil {
				return err
			}
			if live {
				resolved := "changes_json.sport_association"
				association, path = &value, &resolved
			}
		}
		if association == nil {
			if association, path, err = t.actorIndex(ctx, audit.Audit); err != nil {
				return err
			}
		}
		if err = appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "invoice", Key: id, Representation: "Invoice object (" + id + ")",
			Action: 2, Changes: changes, Context: audit.Audit, AssociationID: association, ResolutionPath: path, At: audit.At}); err != nil {
			return err
		}
	}
	return nil
}
