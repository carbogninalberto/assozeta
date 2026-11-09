package repository

import (
	"context"
	"database/sql"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// ExportAllData serves export_all_data. Its querysets have no ordering: the
// queries keep Django's joins and predicates so PostgreSQL answers them with
// the same plans and the rows come back in the same database order. Values
// are read as Python's str() of the Django field value (or NULL for None).
type ExportAllData struct{ db *sqlx.DB }

func NewExportAllData(db *sqlx.DB) *ExportAllData { return &ExportAllData{db: db} }

var _ domain.ExportAllDataStore = (*ExportAllData)(nil)

type exportAllDataTransaction struct{ *ownedTransaction }

func (r *ExportAllData) BeginExportAllData(ctx context.Context) (domain.ExportAllDataTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted, ReadOnly: true})
	if err != nil {
		return nil, err
	}
	return &exportAllDataTransaction{ownedTransaction: tx}, nil
}

func (t *exportAllDataTransaction) Association(ctx context.Context, user string) (*domain.PrintingAssociation, error) {
	rows := []domain.PrintingAssociation{}
	if err := t.tx.SelectContext(ctx, &rows, `SELECT sport_association_id, denomination FROM application_sportassociation
 WHERE NOT deleted AND user_id=$1 LIMIT 21`, user); err != nil {
		return nil, err
	}
	if len(rows) != 1 {
		return nil, domain.ErrAssociationNotFound
	}
	return &rows[0], nil
}

// exportAllDataExpression is the SQL text of str(value) for one column of
// alias (NULL for None): dates 'YYYY-MM-DD', aware UTC datetimes
// 'YYYY-MM-DD HH:MM:SS[.ffffff]+00:00', booleans 'True'/'False', Decimals,
// ints and UUIDs as stored, foreign keys '<Model> object (<pk>)'. JSON and
// choice columns are read raw and finished by the service.
func exportAllDataExpression(alias string, column domain.ExportAllDataColumn) string {
	ref := alias + "." + column.SQL
	if column.FK != "" {
		return "CASE WHEN " + ref + " IS NOT NULL THEN '" + column.FK + " object (' || " + ref + "::text || ')' END"
	}
	switch column.Source {
	case "datetime":
		return "to_char(" + ref + " AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || CASE WHEN date_part('microseconds', " + ref +
			")::bigint % 1000000 <> 0 THEN to_char(" + ref + " AT TIME ZONE 'UTC', '.US') ELSE '' END || '+00:00'"
	case "bool":
		return "CASE WHEN " + ref + " THEN 'True' WHEN NOT " + ref + " THEN 'False' END"
	}
	return ref + "::text"
}

func exportAllDataSelect(alias string, columns []domain.ExportAllDataColumn) []string {
	out := make([]string, len(columns))
	for i, column := range columns {
		out[i] = exportAllDataExpression(alias, column)
	}
	return out
}

// exportAllDataScan reads every column of the query as nullable text.
func (t *exportAllDataTransaction) exportAllDataScan(ctx context.Context, query string, args ...any) ([][]*string, error) {
	rows, err := t.tx.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	columns, err := rows.Columns()
	if err != nil {
		return nil, err
	}
	out := [][]*string{}
	for rows.Next() {
		values := make([]sql.NullString, len(columns))
		targets := make([]any, len(columns))
		for i := range values {
			targets[i] = &values[i]
		}
		if err := rows.Scan(targets...); err != nil {
			return nil, err
		}
		row := make([]*string, len(columns))
		for i, value := range values {
			if value.Valid {
				text := value.String
				row[i] = &text
			}
		}
		out = append(out, row)
	}
	return out, rows.Err()
}

// Subscriptions is scoped_queryset(Subscription.objects) (the group-aware
// manager).filter(sport_association=...).select_related('associate', 'medical').
func (t *exportAllDataTransaction) Subscriptions(ctx context.Context, association string, group, scope *string) ([]domain.ExportAllDataSubscription, error) {
	selected := []string{"s.medical_id IS NOT NULL", "s.associate_id::text"}
	selected = append(selected, exportAllDataSelect("s", domain.ExportAllDataSubscriptionColumns)...)
	selected = append(selected, exportAllDataSelect("m", domain.ExportAllDataMedicalColumns)...)
	selected = append(selected, exportAllDataSelect("a", domain.ExportAllDataAssociateColumns)...)
	rows, err := t.exportAllDataScan(ctx, `SELECT `+strings.Join(selected, ", ")+` FROM application_subscription s
 LEFT OUTER JOIN application_associate a ON s.associate_id = a.associate_id
 LEFT OUTER JOIN application_medicalcertificate m ON s.medical_id = m.medical_id
 WHERE ($2::uuid IS NULL OR s.group_id = $2::uuid) AND ($3::uuid IS NULL OR s.sport_association_id = $3::uuid)
 AND s.sport_association_id = $1::uuid`, association, group, scope)
	if err != nil {
		return nil, err
	}
	out := make([]domain.ExportAllDataSubscription, len(rows))
	sizes := []int{len(domain.ExportAllDataSubscriptionColumns), len(domain.ExportAllDataMedicalColumns)}
	for i, row := range rows {
		at := 2
		out[i] = domain.ExportAllDataSubscription{HasMedical: *row[0] == "true", AssociateID: row[1],
			Subscription: row[at : at+sizes[0]], Medical: row[at+sizes[0] : at+sizes[0]+sizes[1]], Associate: row[at+sizes[0]+sizes[1]:]}
	}
	return out, nil
}

// Tutors is prefetch_related('associate__tutors'): the M2M through the
// relations with Associate's default manager (live, group-aware).
func (t *exportAllDataTransaction) Tutors(ctx context.Context, associates []string, group *string) (map[string][][]*string, error) {
	out := map[string][][]*string{}
	if len(associates) == 0 {
		return out, nil
	}
	rows, err := t.exportAllDataScan(ctx, `SELECT r.associate_id::text, `+strings.Join(exportAllDataSelect("a", domain.ExportAllDataAssociateColumns), ", ")+`
 FROM application_associate a INNER JOIN application_associatetutorrelation r ON a.associate_id = r.tutor_id
 WHERE ($2::uuid IS NULL OR a.group_id = $2::uuid) AND NOT a.deleted AND r.associate_id = ANY($1::uuid[])`, uuidArray(associates), group)
	if err != nil {
		return nil, err
	}
	for _, row := range rows {
		out[*row[0]] = append(out[*row[0]], row[1:])
	}
	return out, nil
}

// Payments is scoped_queryset(Payment.objects) (live, group-aware)
// .filter(sport_association=...).select_related('associate', 'payment_category',
// 'invoice', 'supplier', 'instructor', 'custom_accounts').
func (t *exportAllDataTransaction) Payments(ctx context.Context, association string, group, scope *string) ([]domain.ExportAllDataPayment, error) {
	sections := []struct {
		alias, key string
		columns    []domain.ExportAllDataColumn
	}{
		{"c", "payment_category_id", domain.ExportAllDataCategoryColumns},
		{"k", "custom_account_id", domain.ExportAllDataAccountColumns},
		{"a", "associate_id", domain.ExportAllDataAssociateColumns},
		{"u", "supplier_id", domain.ExportAllDataSupplierColumns},
		{"n", "instructor_id", domain.ExportAllDataInstructorColumns},
		{"i", "invoice_id", domain.ExportAllDataInvoiceColumns},
	}
	selected := []string{"p.payment_id::text", "p.subject::text", "p.course::text"}
	selected = append(selected, exportAllDataSelect("p", domain.ExportAllDataPaymentColumns)...)
	for _, section := range sections {
		selected = append(selected, section.alias+"."+section.key+" IS NOT NULL")
		selected = append(selected, exportAllDataSelect(section.alias, section.columns)...)
	}
	rows, err := t.exportAllDataScan(ctx, `SELECT `+strings.Join(selected, ", ")+` FROM application_payment p
 LEFT OUTER JOIN application_paymentcategory c ON p.payment_category_id = c.payment_category_id
 LEFT OUTER JOIN application_invoice i ON p.invoice_id = i.invoice_id
 LEFT OUTER JOIN application_associate a ON p.associate_id = a.associate_id
 LEFT OUTER JOIN application_supplierandcustomers u ON p.supplier_id = u.supplier_id
 LEFT OUTER JOIN application_instructor n ON p.instructor_id = n.instructor_id
 LEFT OUTER JOIN application_customaccounts k ON p.custom_accounts_id = k.custom_account_id
 WHERE ($2::uuid IS NULL OR p.group_id = $2::uuid) AND NOT p.deleted AND ($3::uuid IS NULL OR p.sport_association_id = $3::uuid)
 AND p.sport_association_id = $1::uuid`, association, group, scope)
	if err != nil {
		return nil, err
	}
	out := make([]domain.ExportAllDataPayment, len(rows))
	for i, row := range rows {
		subject := int64(0)
		for _, digit := range *row[1] {
			subject = subject*10 + int64(digit-'0')
		}
		at := 3 + len(domain.ExportAllDataPaymentColumns)
		payment := domain.ExportAllDataPayment{ID: *row[0], Subject: subject, Course: row[2], Payment: row[3:at]}
		for s, section := range sections {
			present := *row[at] == "true"
			at++
			if present {
				payment.Related[s] = row[at : at+len(section.columns)]
			}
			at += len(section.columns)
		}
		out[i] = payment
	}
	return out, nil
}

// CourseSubscription is scoped_queryset(CourseSubscription.objects) (live)
// .get(payment=...) with its course's title; during impersonation the
// subscription and the course must belong to the association.
func (t *exportAllDataTransaction) CourseSubscription(ctx context.Context, payment string, scope *string) (domain.ExportAllDataCourse, error) {
	return t.exportAllDataCourse(ctx, `SELECT c.title FROM application_coursesubscription cs JOIN application_course c ON c.course_id = cs.course_id
 LEFT JOIN application_subscription s ON s.subscription_id = cs.subscription_id
 WHERE NOT cs.deleted AND cs.payment_id = $1::uuid
 AND ($2::uuid IS NULL OR (s.sport_association_id = $2::uuid AND c.sport_association_id = $2::uuid)) LIMIT 21`, payment, scope)
}

// CourseInstallment is scoped_queryset(CourseSubscriptionInstallment.objects)
// (live, group-aware).get(payment=...) with course_subscription.course.title.
func (t *exportAllDataTransaction) CourseInstallment(ctx context.Context, payment string, group, scope *string) (domain.ExportAllDataCourse, error) {
	return t.exportAllDataCourse(ctx, `SELECT c.title FROM application_coursesubscriptioninstallment ci
 JOIN application_coursesubscription cs ON cs.course_subscription_id = ci.course_subscription_id
 JOIN application_course c ON c.course_id = cs.course_id LEFT JOIN application_subscription s ON s.subscription_id = cs.subscription_id
 WHERE NOT ci.deleted AND ci.payment_id = $1::uuid AND ($3::uuid IS NULL OR ci.group_id = $3::uuid)
 AND ($2::uuid IS NULL OR (s.sport_association_id = $2::uuid AND c.sport_association_id = $2::uuid)) LIMIT 21`, payment, scope, group)
}

func (t *exportAllDataTransaction) exportAllDataCourse(ctx context.Context, query string, args ...any) (domain.ExportAllDataCourse, error) {
	titles := []sql.NullString{}
	if err := t.tx.SelectContext(ctx, &titles, query, args...); err != nil {
		return domain.ExportAllDataCourse{}, err
	}
	result := domain.ExportAllDataCourse{Matches: len(titles)}
	if len(titles) == 1 && titles[0].Valid {
		result.Title = &titles[0].String
	}
	return result, nil
}
