package repository

import (
	"context"
	"errors"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type PaymentReads struct {
	db       *sqlx.DB
	personas *Personas
}

func NewPaymentReads(db *sqlx.DB) *PaymentReads {
	return &PaymentReads{db: db, personas: NewPersonas(db)}
}

var _ domain.PaymentReadsStore = (*PaymentReads)(nil)

func (r *PaymentReads) UserAssociation(ctx context.Context, user string) (*string, error) {
	ids := []string{}
	if err := r.db.SelectContext(ctx, &ids, `SELECT sport_association_id FROM application_sportassociation WHERE user_id=$1`, user); err != nil {
		return nil, err
	}
	if len(ids) == 0 {
		return nil, nil
	}
	return &ids[0], nil
}

func (r *PaymentReads) ScopedAssociation(ctx context.Context, user string, scope *string) (*string, error) {
	id, err := paymentCategoryAssociation(ctx, r.db, user, scope, false)
	if errors.Is(err, domain.ErrAssociationNotFound) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &id, nil
}

// StripeInstanceSettings is effective_integration('stripe')'s instance
// branch: the first configuration (smallest key) when self-hosted, with any
// integration settings that could hold Stripe's (or are not an object).
func (r *PaymentReads) StripeInstanceSettings(ctx context.Context) (bool, error) {
	values := []bool{}
	err := r.db.SelectContext(ctx, &values, `SELECT COALESCE(self_hosted AND integration_settings IS NOT NULL
 AND integration_settings NOT IN ('null'::jsonb, '{}'::jsonb)
 AND (jsonb_typeof(integration_settings) <> 'object' OR integration_settings ? 'stripe'), FALSE)
 FROM instance_instanceconfiguration ORDER BY id LIMIT 1`)
	if err != nil || len(values) == 0 {
		return false, err
	}
	return values[0], nil
}

// paymentReadsText is Django's icontains operand on PostgreSQL: field::text,
// a timestamptz rendered in the connection's UTC zone.
func paymentReadsText(column string, timestamp bool) string {
	if timestamp {
		return "((" + column + " AT TIME ZONE 'UTC')::text || '+00')"
	}
	return column + "::text"
}

type paymentReadsField struct {
	column    string
	timestamp bool
	// date: the field name contains 'date' or 'created' (date variations);
	// permuted: it contains 'name', 'address', 'city' or 'notes'.
	date, permuted bool
}

func paymentReadsPerson(alias string) []paymentReadsField {
	fields := []paymentReadsField{}
	for _, name := range []string{"first_name", "last_name", "tax_code", "email", "phone", "phone_2", "phone_3", "phone_4", "born_city",
		"born_date", "address_city", "address", "notes", "nationality", "id_number"} {
		permuted := strings.Contains(name, "name") || strings.Contains(name, "address") || strings.Contains(name, "city") ||
			strings.Contains(name, "notes")
		fields = append(fields, paymentReadsField{column: alias + "." + name, date: name == "born_date", permuted: permuted})
	}
	return fields
}

// paymentReadsSearchFields are get_controlled_searchable_fields(Payment):
// the concrete non-relation fields, the associate's and its tutors' listed
// fields, and the supplier's and instructor's fields that exist.
var paymentReadsSearchFields = func() []paymentReadsField {
	fields := []paymentReadsField{}
	for _, name := range []string{"payment_id", "type", "subject", "amount", "creation_date", "payment_date", "paid", "expense",
		"description", "meta_payment_categories", "payment_intent_id", "archived", "invoice_fiscal_code", "meta", "notes", "deleted",
		"course"} {
		timestamp := name == "creation_date" || name == "payment_date"
		fields = append(fields, paymentReadsField{column: "p." + name, timestamp: timestamp, date: timestamp, permuted: name == "notes"})
	}
	fields = append(fields, paymentReadsPerson("sa")...)
	fields = append(fields, paymentReadsPerson("st")...)
	for _, name := range []string{"name", "email", "address", "city", "tax_code", "vat_number"} {
		fields = append(fields, paymentReadsField{column: "ss." + name, permuted: name == "name" || name == "address" || name == "city"})
	}
	for _, name := range []string{"first_name", "last_name", "email", "phone", "tax_code", "address"} {
		fields = append(fields, paymentReadsField{column: "si." + name, permuted: name == "first_name" || name == "last_name" || name == "address"})
	}
	return fields
}()

// paymentReadsSearchJoins are the search's LEFT JOINs (one row per tutor relation).
const paymentReadsSearchJoins = ` FROM (SELECT 1) d
 LEFT JOIN application_associate sa ON sa.associate_id=p.associate_id
 LEFT JOIN application_associatetutorrelation sr ON sr.associate_id=sa.associate_id
 LEFT JOIN application_associate st ON st.associate_id=sr.tutor_id
 LEFT JOIN application_supplierandcustomers ss ON ss.supplier_id=p.supplier_id
 LEFT JOIN application_instructor si ON si.instructor_id=p.instructor_id`

func paymentReadsSmartCondition(search *domain.PaymentReadsSmartSearch, args *personaArgs) string {
	words := make([]string, len(search.Words))
	for i, word := range search.Words {
		parameter := args.add(word)
		terms := []string{}
		for _, field := range paymentReadsSearchFields {
			terms = append(terms, personaLike(paymentReadsText(field.column, field.timestamp), parameter))
		}
		for _, variation := range search.Dates[i] {
			variationParameter := args.add(variation)
			for _, field := range paymentReadsSearchFields {
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
			for _, field := range paymentReadsSearchFields {
				if field.permuted {
					terms = append(terms, personaLike(paymentReadsText(field.column, field.timestamp), parameter))
				}
			}
		}
		condition = "(" + condition + " OR " + strings.Join(terms, " OR ") + ")"
	}
	return condition
}

// paymentReadsMemberJoins are subscription_payments' search joins: forward
// relations, then the reverse course subscription and installment relations
// (each matching row is a result row, as Django does without distinct()).
const paymentReadsMemberJoins = `
 LEFT JOIN application_associate ma ON ma.associate_id=p.associate_id
 LEFT JOIN application_paymentcategory mc ON mc.payment_category_id=p.payment_category_id
 LEFT JOIN application_supplierandcustomers msu ON msu.supplier_id=p.supplier_id
 LEFT JOIN application_instructor mi ON mi.instructor_id=p.instructor_id
 LEFT JOIN application_customaccounts mca ON mca.custom_account_id=p.custom_accounts_id
 LEFT JOIN application_coursesubscription mcs ON mcs.payment_id=p.payment_id
 LEFT JOIN application_course mco ON mco.course_id=mcs.course_id
 LEFT JOIN application_coursesubscriptioninstallment mci ON mci.payment_id=p.payment_id
 LEFT JOIN application_coursesubscription mcs2 ON mcs2.course_subscription_id=mci.course_subscription_id
 LEFT JOIN application_course mco2 ON mco2.course_id=mcs2.course_id`

var paymentReadsMemberFields = []string{"p.payment_id::text", "p.notes::text", "p.meta::text", "p.description::text", "mc.name::text",
	"(COALESCE(ma.first_name, '') || ' ' || COALESCE(ma.last_name, ''))", "msu.name::text", "mi.first_name::text", "mi.last_name::text",
	"ma.first_name::text", "ma.last_name::text", "ma.tax_code::text", paymentReadsText("p.payment_date", true), "p.type::text",
	"p.subject::text", paymentReadsText("p.creation_date", true), "mca.name::text", "mco.title::text", "mco2.title::text"}

// paymentReadsQuery is FROM ... WHERE ... of the filter.
func paymentReadsQuery(filter domain.PaymentReadsFilter, args *personaArgs) string {
	from := ` FROM application_payment p`
	where := []string{"NOT p.deleted", "p.sport_association_id=" + args.add(filter.AssociationID) + "::uuid"}
	group := args.add(filter.Group)
	where = append(where, "("+group+"::uuid IS NULL OR p.group_id="+group+"::uuid)")
	if filter.Scope != nil {
		where = append(where, "p.sport_association_id="+args.add(*filter.Scope)+"::uuid")
	}
	if filter.Archived != nil {
		where = append(where, "p.archived="+args.add(*filter.Archived))
	}
	if filter.Empty {
		where = append(where, "FALSE")
	}
	if filter.Member != nil {
		member := "p.associate_id=" + args.add(*filter.Member) + "::uuid"
		if filter.MemberTaxCode != nil {
			member = "(" + member + " OR EXISTS(SELECT 1 FROM application_associate mt WHERE mt.associate_id=p.associate_id AND UPPER(mt.tax_code::text)=UPPER(" +
				args.add(*filter.MemberTaxCode) + ")))"
		}
		where = append(where, member)
	}
	if filter.AssociateID != nil {
		where = append(where, "p.associate_id="+args.add(*filter.AssociateID)+"::uuid")
	}
	if filter.From != nil && filter.To != nil {
		start, end := args.add(*filter.From), args.add(*filter.To)
		where = append(where, "((p.payment_date BETWEEN "+start+" AND "+end+" AND p.payment_date IS NOT NULL) OR (p.creation_date BETWEEN "+
			start+" AND "+end+" AND p.payment_date IS NULL))")
	}
	if filter.Subject != nil {
		where = append(where, "p.subject="+args.add(*filter.Subject))
	}
	if filter.Type != nil {
		where = append(where, "p.type="+args.add(*filter.Type))
	}
	if filter.Paid != nil {
		where = append(where, "p.paid="+args.add(*filter.Paid))
	}
	if filter.Expense != nil {
		where = append(where, "p.expense="+args.add(*filter.Expense))
	}
	if filter.NoCategory {
		where = append(where, "p.payment_category_id IS NULL")
	}
	if filter.Categories != nil {
		where = append(where, "p.payment_category_id = ANY(string_to_array("+args.add(strings.Join(filter.Categories, ","))+", ',')::uuid[])")
	}
	if filter.NoAccount {
		where = append(where, "p.custom_accounts_id IS NULL")
	}
	if filter.Account != nil {
		where = append(where, "p.custom_accounts_id="+args.add(*filter.Account)+"::uuid")
	}
	if filter.AmountText != nil {
		where = append(where, "("+personaLike("p.amount::text", args.add(paymentReadsContains(*filter.AmountText)))+" OR p.amount="+args.add(*filter.AmountExact)+"::numeric)")
	}
	if filter.Smart != nil && len(filter.Smart.Words) > 0 {
		where = append(where, "EXISTS(SELECT 1"+paymentReadsSearchJoins+" WHERE "+paymentReadsSmartCondition(filter.Smart, args)+")")
	}
	if filter.MemberText != nil {
		from += paymentReadsMemberJoins
		parameter := args.add(paymentReadsContains(*filter.MemberText))
		terms := make([]string, len(paymentReadsMemberFields))
		for i, field := range paymentReadsMemberFields {
			terms[i] = personaLike(field, parameter)
		}
		where = append(where, "("+strings.Join(terms, " OR ")+")")
	}
	return from + "\n WHERE " + strings.Join(where, " AND ")
}

// paymentReadsContains is icontains' pattern: the value escaped and wrapped in %.
func paymentReadsContains(value string) string {
	return "%" + strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`).Replace(value) + "%"
}

// paymentReadsColumns are the Payment columns the service may order by.
var paymentReadsColumns = map[string]bool{"payment_id": true, "type": true, "subject": true, "amount": true, "creation_date": true,
	"payment_date": true, "paid": true, "expense": true, "description": true, "meta_payment_categories": true, "payment_intent_id": true,
	"archived": true, "invoice_fiscal_code": true, "meta": true, "notes": true, "deleted": true, "course": true, "payment_category_id": true,
	"invoice_id": true, "user_id": true, "associate_id": true, "supplier_id": true, "instructor_id": true, "sport_association_id": true,
	"custom_accounts_id": true, "signature_id": true, "group_id": true}

func (r *PaymentReads) Compressed(ctx context.Context, filter domain.PaymentReadsFilter) ([]domain.PaymentReadsCompressed, error) {
	args := personaArgs{}
	body := paymentReadsQuery(filter, &args)
	rows := []domain.PaymentReadsCompressed{}
	err := r.db.SelectContext(ctx, &rows, `SELECT p.payment_id, p.amount::text AS amount, p.payment_date, p.paid, p.associate_id, p.supplier_id,
 p.instructor_id, ca.first_name AS associate_first_name, ca.last_name AS associate_last_name, cs.name AS supplier_name,
 ci.first_name AS instructor_first_name, ci.last_name AS instructor_last_name, cc.name AS account_name
 FROM application_payment p
 LEFT JOIN application_associate ca ON ca.associate_id=p.associate_id
 LEFT JOIN application_supplierandcustomers cs ON cs.supplier_id=p.supplier_id
 LEFT JOIN application_instructor ci ON ci.instructor_id=p.instructor_id
 LEFT JOIN application_customaccounts cc ON cc.custom_account_id=p.custom_accounts_id
 WHERE p.payment_id IN (SELECT p.payment_id`+body+`) ORDER BY p.creation_date DESC`, args...)
	return rows, err
}

func (r *PaymentReads) CountPayments(ctx context.Context, filter domain.PaymentReadsFilter) (int64, error) {
	args := personaArgs{}
	body := paymentReadsQuery(filter, &args)
	var count int64
	err := r.db.GetContext(ctx, &count, "SELECT COUNT(*)"+body, args...)
	return count, err
}

func (r *PaymentReads) Payments(ctx context.Context, filter domain.PaymentReadsFilter, limit, offset int64) ([]string, error) {
	args := personaArgs{}
	statement := "SELECT p.payment_id" + paymentReadsQuery(filter, &args)
	switch {
	case filter.Random:
		statement += " ORDER BY RANDOM()"
	case len(filter.Order) > 0:
		terms := make([]string, len(filter.Order))
		for i, order := range filter.Order {
			if !paymentReadsColumns[order.Column] {
				return nil, domain.ErrPaymentReadsNotPorted
			}
			terms[i] = "p." + order.Column
			if order.Descending {
				terms[i] += " DESC"
			}
		}
		statement += " ORDER BY " + strings.Join(terms, ", ")
	}
	statement += " LIMIT " + args.add(limit) + " OFFSET " + args.add(offset)
	ids := []string{}
	err := r.db.SelectContext(ctx, &ids, statement, args...)
	return ids, err
}

func (r *PaymentReads) Hours(ctx context.Context, ids []string) ([]domain.PaymentReadsHours, error) {
	rows := []domain.PaymentReadsHours{}
	if len(ids) == 0 {
		return rows, nil
	}
	err := r.db.SelectContext(ctx, &rows, `SELECT h.payment_id, h.document_id, d.token::text AS document_token
 FROM application_instructorhours h LEFT JOIN docmanager_document d ON d.document_id=h.document_id
 WHERE h.payment_id = ANY(string_to_array($1, ',')::uuid[])`, strings.Join(ids, ","))
	return rows, err
}

// paymentReadsMultiplicity is the number of search join rows a payment
// contributes to an aggregate over the joins (1 without a search).
func paymentReadsMultiplicity(filter domain.PaymentReadsFilter, args *personaArgs) string {
	if filter.Smart == nil || len(filter.Smart.Words) == 0 {
		return "1"
	}
	return "(SELECT COUNT(*)" + paymentReadsSearchJoins + " WHERE " + paymentReadsSmartCondition(filter.Smart, args) + ")"
}

func (r *PaymentReads) Totals(ctx context.Context, filter domain.PaymentReadsFilter) (domain.PaymentReadsTotals, error) {
	args := personaArgs{}
	body := paymentReadsQuery(filter, &args)
	var totals domain.PaymentReadsTotals
	sum := func(condition, name string) string {
		return "SUM(CASE WHEN " + condition + " THEN p.amount ELSE 0 END)::text AS " + name
	}
	err := r.db.GetContext(ctx, &totals, "SELECT COUNT(p.payment_id) AS total_transactions, "+strings.Join([]string{
		sum("p.expense", "current_expenses"), sum("NOT p.expense", "current_income"), sum("p.expense AND p.paid", "monthly_expenses"),
		sum("p.expense AND NOT p.paid", "monthly_to_pay"), sum("NOT p.expense AND p.paid", "monthly_income"),
		sum("NOT p.expense AND NOT p.paid", "monthly_to_cash_in")}, ", ")+body, args...)
	return totals, err
}

func (r *PaymentReads) AccountChanges(ctx context.Context, filter domain.PaymentReadsFilter) ([]domain.PaymentReadsAccountChange, error) {
	args := personaArgs{}
	body := paymentReadsQuery(filter, &args)
	multiplicity := paymentReadsMultiplicity(filter, &args)
	rows := []domain.PaymentReadsAccountChange{}
	err := r.db.SelectContext(ctx, &rows, `SELECT x.account_type, SUM(x.change * x.multiplicity)::text AS net_change FROM (
 SELECT ca.account_type, p.creation_date, CASE WHEN p.expense THEN -p.amount WHEN NOT p.expense THEN p.amount ELSE 0 END AS change,
 `+multiplicity+` AS multiplicity
 FROM application_payment p LEFT JOIN application_customaccounts ca ON ca.custom_account_id=p.custom_accounts_id
 WHERE p.paid AND p.payment_id IN (SELECT p.payment_id`+body+`)) x GROUP BY x.account_type, x.creation_date`, args...)
	return rows, err
}

func (r *PaymentReads) SubscriptionOf(ctx context.Context, id string, group, scope *string) (*domain.PaymentReadsSubscription, error) {
	rows := []domain.PaymentReadsSubscription{}
	err := r.db.SelectContext(ctx, &rows, `SELECT s.sport_association_id, s.associate_id, a.associate_id IS NOT NULL AS associate_exists, a.tax_code
 FROM application_subscription s LEFT JOIN application_associate a ON a.associate_id=s.associate_id
 WHERE s.subscription_id=$1 AND ($2::uuid IS NULL OR s.group_id=$2::uuid) AND ($3::uuid IS NULL OR s.sport_association_id=$3::uuid)
 ORDER BY s.subscription_id LIMIT 1`, id, group, scope)
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	return &rows[0], nil
}

// Link is subscription_payments' lookups for a course payment: the first
// course subscription (all_objects(), by key), else the first installment
// (all_objects(), group-aware, by key) and its course subscription, else the
// first carnet holding (by key).
func (r *PaymentReads) Link(ctx context.Context, payment string, group, scope *string) (domain.PaymentReadsLink, error) {
	type course struct {
		ID      string  `db:"course_id"`
		Title   *string `db:"title"`
		Deleted bool    `db:"deleted"`
	}
	found := func(query string, args ...any) (*course, error) {
		rows := []course{}
		if err := r.db.SelectContext(ctx, &rows, query, args...); err != nil || len(rows) == 0 {
			return nil, err
		}
		return &rows[0], nil
	}
	row, err := found(`SELECT co.course_id, co.title, co.deleted FROM application_coursesubscription cs
 JOIN application_course co ON co.course_id=cs.course_id LEFT JOIN application_subscription su ON su.subscription_id=cs.subscription_id
 WHERE cs.payment_id=$1 AND ($2::uuid IS NULL OR (su.sport_association_id=$2::uuid AND co.sport_association_id=$2::uuid))
 ORDER BY cs.course_subscription_id LIMIT 1`, payment, scope)
	if err == nil && row == nil {
		row, err = found(`SELECT co.course_id, co.title, co.deleted FROM application_coursesubscriptioninstallment ci
 JOIN application_coursesubscription cs ON cs.course_subscription_id=ci.course_subscription_id
 JOIN application_course co ON co.course_id=cs.course_id LEFT JOIN application_subscription su ON su.subscription_id=cs.subscription_id
 WHERE ci.payment_id=$1 AND ($2::uuid IS NULL OR ci.group_id=$2::uuid)
  AND ($3::uuid IS NULL OR (su.sport_association_id=$3::uuid AND co.sport_association_id=$3::uuid))
 ORDER BY ci.course_subscription_installment_id LIMIT 1`, payment, group, scope)
	}
	if err != nil {
		return domain.PaymentReadsLink{}, err
	}
	if row != nil {
		return domain.PaymentReadsLink{Found: true, CourseID: &row.ID, Title: row.Title, CourseDeleted: row.Deleted}, nil
	}
	holdings := []struct {
		Carnet *string `db:"carnet_id"`
		Title  *string `db:"title"`
	}{}
	if err := r.db.SelectContext(ctx, &holdings, `SELECT h.carnet_id_id AS carnet_id, k.title FROM application_carnetsubscription h
 LEFT JOIN application_carnet k ON k.carnet_id=h.carnet_id_id LEFT JOIN application_subscription su ON su.subscription_id=h.subscription_id
 WHERE h.payment_id=$1 AND ($2::uuid IS NULL OR (su.sport_association_id=$2::uuid AND k.sport_association_id=$2::uuid))
 ORDER BY h.carnet_subscription_id LIMIT 1`, payment, scope); err != nil {
		return domain.PaymentReadsLink{}, err
	}
	if len(holdings) == 0 {
		return domain.PaymentReadsLink{}, nil
	}
	if holdings[0].Carnet == nil {
		return domain.PaymentReadsLink{}, domain.ErrPaymentReadsServer // carnet_id is None
	}
	return domain.PaymentReadsLink{Found: true, Carnet: true, CourseID: holdings[0].Carnet, Title: holdings[0].Title}, nil
}

// Render loads what PaymentOptimizedSerializer reads for each distinct id.
func (r *PaymentReads) Render(ctx context.Context, ids []string, group *string) (map[string]domain.PaymentReadsRender, error) {
	result := map[string]domain.PaymentReadsRender{}
	if len(ids) == 0 {
		return result, nil
	}
	list := strings.Join(ids, ",")
	payments := []domain.InvoicePartyPayment{}
	if err := r.db.SelectContext(ctx, &payments, `SELECT `+invoicePartyPaymentColumns+` FROM application_payment p
 LEFT JOIN application_customaccounts a ON a.custom_account_id=p.custom_accounts_id
 LEFT JOIN application_paymentcategory c ON c.payment_category_id=p.payment_category_id
 WHERE p.payment_id = ANY(string_to_array($1, ',')::uuid[])`, list); err != nil {
		return nil, err
	}
	associates := map[string]domain.PaymentReadsAssociate{}
	rows := []domain.PaymentReadsAssociate{}
	if err := r.db.SelectContext(ctx, &rows, `SELECT a.associate_id, a.first_name, a.last_name, a.is_minor FROM application_associate a
 WHERE a.associate_id IN (SELECT associate_id FROM application_payment WHERE payment_id = ANY(string_to_array($1, ',')::uuid[]))`, list); err != nil {
		return nil, err
	}
	associateIDs := []string{}
	for _, row := range rows {
		associates[row.ID] = row
		associateIDs = append(associateIDs, row.ID)
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
	instructors := map[string]domain.InstructorRow{}
	instructorRows := []domain.InstructorRow{}
	if err := r.db.SelectContext(ctx, &instructorRows, instructorColumns+` WHERE i.instructor_id IN (SELECT instructor_id FROM application_payment
 WHERE payment_id = ANY(string_to_array($1, ',')::uuid[]))`, list); err != nil {
		return nil, err
	}
	for _, row := range instructorRows {
		instructors[row.ID] = row
	}
	suppliers := map[string]domain.Supplier{}
	supplierRows := []domain.Supplier{}
	if err := r.db.SelectContext(ctx, &supplierRows, `SELECT `+supplierColumns+` FROM application_supplierandcustomers WHERE supplier_id IN
 (SELECT supplier_id FROM application_payment WHERE payment_id = ANY(string_to_array($1, ',')::uuid[]))`, list); err != nil {
		return nil, err
	}
	for _, row := range supplierRows {
		suppliers[row.ID] = row
	}
	invoices := map[string]domain.PaymentReadsInvoice{}
	invoiceRows := []domain.PaymentReadsInvoice{}
	if err := r.db.SelectContext(ctx, &invoiceRows, `SELECT v.invoice_id, v.document_pdf_id, d.token::text AS document_token,
 v.sport_association_id, v.creation_date, v.membership_fee::text AS membership_fee, v.activity_fee::text AS activity_fee,
 v.meta_payment_categories::text AS meta_payment_categories, v.description, v.number, v.archived, v.cancelled, v.selected_tutor_id,
 v.meta::text AS meta, v.imported_from_associami, v.group_id, v.deleted,
 COALESCE((SELECT json_agg(l.document_id ORDER BY l.id) FROM application_invoice_attached_documents l WHERE l.invoice_id=v.invoice_id),
  '[]'::json)::text AS attached_documents
 FROM application_invoice v LEFT JOIN docmanager_document d ON d.document_id=v.document_pdf_id
 WHERE v.invoice_id IN (SELECT invoice_id FROM application_payment WHERE payment_id = ANY(string_to_array($1, ',')::uuid[]))`, list); err != nil {
		return nil, err
	}
	for _, row := range invoiceRows {
		invoices[row.ID] = row
	}
	attachments := []struct {
		Payment string `db:"payment_id"`
		domain.InvoicePartyDocument
	}{}
	if err := r.db.SelectContext(ctx, &attachments, `SELECT l.payment_id, d.document_id, d.creation_date, d.filepath, d.filename,
 d.file_size_bytes, d.token::text AS token FROM application_payment_attachments l JOIN docmanager_document d ON d.document_id=l.document_id
 WHERE l.payment_id = ANY(string_to_array($1, ',')::uuid[]) ORDER BY l.id`, list); err != nil {
		return nil, err
	}
	for _, payment := range payments {
		render := domain.PaymentReadsRender{Payment: payment, Attachments: []domain.InvoicePartyDocument{}}
		for _, attachment := range attachments {
			if attachment.Payment == payment.ID {
				render.Attachments = append(render.Attachments, attachment.InvoicePartyDocument)
			}
		}
		if payment.AssociateID != nil {
			if associate, ok := associates[*payment.AssociateID]; ok {
				render.Associate = &associate
				for _, relation := range relations {
					if relation.AssociateID == associate.ID {
						render.Relations = append(render.Relations, relation)
					}
				}
				render.Second, render.Third = second, third
			}
		}
		if payment.InstructorID != nil {
			if row, ok := instructors[*payment.InstructorID]; ok {
				render.Instructor = &row
			}
		}
		if payment.SupplierID != nil {
			if row, ok := suppliers[*payment.SupplierID]; ok {
				render.Payment.Supplier = &row
			}
		}
		if payment.InvoiceID != nil {
			if row, ok := invoices[*payment.InvoiceID]; ok {
				render.Invoice = &row
			}
		}
		if render.Payment.Subscription, err = r.subscription(ctx, payment, group); err != nil {
			return nil, err
		}
		if render.Course, err = r.course(ctx, payment, group); err != nil {
			return nil, err
		}
		result[payment.ID] = render
	}
	return result, nil
}

// paymentReadsOne is a .get(): nil for DoesNotExist, an error for MultipleObjectsReturned.
func (r *PaymentReads) one(ctx context.Context, query string, args ...any) (found bool, value *string, err error) {
	values := []*string{}
	if err := r.db.SelectContext(ctx, &values, query+` LIMIT 2`, args...); err != nil {
		return false, nil, err
	}
	switch len(values) {
	case 0:
		return false, nil, nil
	case 1:
		return true, values[0], nil
	}
	return false, nil, domain.ErrPaymentReadsServer
}

// subscription is PaymentOptimizedSerializer.get_subscription_id.
func (r *PaymentReads) subscription(ctx context.Context, payment domain.InvoicePartyPayment, group *string) (*string, error) {
	// A related subscription that is None raises AttributeError.
	required := func(found bool, value *string, err error) (bool, *string, error) {
		if err == nil && found && value == nil {
			return false, nil, domain.ErrPaymentReadsServer
		}
		return found, value, err
	}
	first := func(query string, args ...any) (*string, error) {
		values := []string{}
		if err := r.db.SelectContext(ctx, &values, query, args...); err != nil || len(values) == 0 {
			return nil, err
		}
		return &values[0], nil
	}
	switch payment.Subject {
	case 1:
		found, value, err := r.one(ctx, `SELECT subscription_id::text FROM application_subscription
 WHERE payment_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR group_id=$2::uuid)`, payment.ID, group)
		if err != nil || found {
			return value, err
		}
		// The fallback runs inside except Exception: any error is None.
		value, err = first(`SELECT subscription_id::text FROM application_subscription
 WHERE associate_id IS NOT DISTINCT FROM $1::uuid AND NOT deleted AND NOT archived AND ($2::uuid IS NULL OR group_id=$2::uuid)
 ORDER BY creation_date LIMIT 1`, payment.AssociateID, group)
		return value, err
	case 2:
		found, value, err := required(r.one(ctx, `SELECT cs.subscription_id::text FROM application_coursesubscriptioninstallment i
 JOIN application_coursesubscription cs ON cs.course_subscription_id=i.course_subscription_id
 WHERE i.payment_id=$1 AND NOT i.deleted AND ($2::uuid IS NULL OR i.group_id=$2::uuid)`, payment.ID, group))
		if err != nil || found {
			return value, err
		}
		found, value, err = required(r.one(ctx, `SELECT subscription_id::text FROM application_coursesubscription
 WHERE payment_id=$1 AND NOT deleted`, payment.ID))
		if err != nil || found {
			return value, err
		}
		_, value, err = required(r.one(ctx, `SELECT subscription_id::text FROM application_carnetsubscription WHERE payment_id=$1`, payment.ID))
		return value, err
	case 0:
		return first(`SELECT subscription_id::text FROM application_subscription
 WHERE associate_id IS NOT DISTINCT FROM $1::uuid AND NOT archived AND ($2::uuid IS NULL OR group_id=$2::uuid)
 ORDER BY creation_date LIMIT 1`, payment.AssociateID, group)
	}
	return nil, nil
}

// course is PaymentOptimizedSerializer.get_course's related lookups.
func (r *PaymentReads) course(ctx context.Context, payment domain.InvoicePartyPayment, group *string) (*domain.PaymentReadsCourse, error) {
	if payment.Subject != 2 {
		return nil, nil
	}
	lookup := func(query string, args ...any) (*domain.PaymentReadsCourse, bool, error) {
		rows := []domain.PaymentReadsCourse{}
		if err := r.db.SelectContext(ctx, &rows, query+` LIMIT 2`, args...); err != nil {
			return nil, false, err
		}
		switch len(rows) {
		case 0:
			return nil, false, nil
		case 1:
			return &rows[0], true, nil
		}
		return nil, false, domain.ErrPaymentReadsServer
	}
	row, found, err := lookup(`SELECT co.course_id, co.title FROM application_coursesubscription cs
 JOIN application_course co ON co.course_id=cs.course_id WHERE cs.payment_id=$1 AND NOT cs.deleted`, payment.ID)
	if err != nil || found {
		return row, err
	}
	row, _, err = lookup(`SELECT co.course_id, co.title FROM application_coursesubscriptioninstallment i
 JOIN application_coursesubscription cs ON cs.course_subscription_id=i.course_subscription_id
 JOIN application_course co ON co.course_id=cs.course_id
 WHERE i.payment_id=$1 AND NOT i.deleted AND ($2::uuid IS NULL OR i.group_id=$2::uuid)`, payment.ID, group)
	return row, err
}
