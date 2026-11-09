package repository

import (
	"context"
	"errors"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// PaymentExport reads what payment_list_export renders.
type PaymentExport struct {
	db *sqlx.DB
}

func NewPaymentExport(db *sqlx.DB) *PaymentExport {
	return &PaymentExport{db: db}
}

var _ domain.PaymentExportStore = (*PaymentExport)(nil)

func (r *PaymentExport) ScopedAssociation(ctx context.Context, user string, scope *string) (*domain.PaymentExportAssociation, error) {
	id, err := paymentCategoryAssociation(ctx, r.db, user, scope, false)
	if errors.Is(err, domain.ErrAssociationNotFound) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var association domain.PaymentExportAssociation
	err = r.db.GetContext(ctx, &association, `SELECT sport_association_id, denomination FROM application_sportassociation
 WHERE sport_association_id=$1`, id)
	return &association, err
}

func (r *PaymentExport) Payments(ctx context.Context, filter domain.PaymentReadsFilter) ([]domain.PaymentExportRow, error) {
	args := personaArgs{}
	body := paymentReadsQuery(filter, &args)
	rows := []domain.PaymentExportRow{}
	err := r.db.SelectContext(ctx, &rows, `SELECT p.payment_id, p.type, p.subject, p.amount::text AS amount, p.creation_date, p.payment_date,
 p.paid, p.expense, p.description, p.meta_payment_categories::text AS meta_payment_categories, p.notes, p.course::text AS course,
 p.custom_accounts_id, ca.name AS account_name, pc.payment_category_id IS NOT NULL AS has_category, pc.name AS category_name,
 iv.invoice_id IS NOT NULL AS has_invoice, iv.number AS invoice_number,
 aa.associate_id IS NOT NULL AS has_associate, aa.first_name AS associate_first_name, aa.last_name AS associate_last_name,
 ii.instructor_id IS NOT NULL AS has_instructor, ii.first_name AS instructor_first_name, ii.last_name AS instructor_last_name,
 ss.supplier_id IS NOT NULL AS has_supplier, ss.name AS supplier_name
 FROM application_payment p
 LEFT JOIN application_customaccounts ca ON ca.custom_account_id=p.custom_accounts_id
 LEFT JOIN application_paymentcategory pc ON pc.payment_category_id=p.payment_category_id
 LEFT JOIN application_invoice iv ON iv.invoice_id=p.invoice_id
 LEFT JOIN application_associate aa ON aa.associate_id=p.associate_id
 LEFT JOIN application_instructor ii ON ii.instructor_id=p.instructor_id
 LEFT JOIN application_supplierandcustomers ss ON ss.supplier_id=p.supplier_id
 WHERE p.payment_id IN (SELECT p.payment_id`+body+`) ORDER BY p.payment_date DESC`, args...)
	return rows, err
}

type paymentExportTitle struct {
	PaymentID string  `db:"payment_id"`
	Title     *string `db:"title"`
}

// Courses follows the prefetches: coursesubscription_set and
// coursesubscriptioninstallment_set through all_objects() (the installments'
// manager is group-aware) and scoped_queryset; .first() orders by the key.
func (r *PaymentExport) Courses(ctx context.Context, ids []string, group, scope *string) (map[string]domain.PaymentExportCourse, error) {
	result := map[string]domain.PaymentExportCourse{}
	if len(ids) == 0 {
		return result, nil
	}
	joined := strings.Join(ids, ",")
	subscriptions := []paymentExportTitle{}
	err := r.db.SelectContext(ctx, &subscriptions, `SELECT DISTINCT ON (cs.payment_id) cs.payment_id, c.title
 FROM application_coursesubscription cs JOIN application_course c ON c.course_id=cs.course_id
 WHERE cs.payment_id = ANY(string_to_array($1, ',')::uuid[])
 AND ($2::uuid IS NULL OR (c.sport_association_id=$2::uuid AND EXISTS(SELECT 1 FROM application_subscription s
 WHERE s.subscription_id=cs.subscription_id AND s.sport_association_id=$2::uuid)))
 ORDER BY cs.payment_id, cs.course_subscription_id`, joined, scope)
	if err != nil {
		return nil, err
	}
	for _, row := range subscriptions {
		result[row.PaymentID] = domain.PaymentExportCourse{Found: true, Title: row.Title}
	}
	installments := []paymentExportTitle{}
	err = r.db.SelectContext(ctx, &installments, `SELECT DISTINCT ON (ci.payment_id) ci.payment_id, c.title
 FROM application_coursesubscriptioninstallment ci
 JOIN application_coursesubscription cs ON cs.course_subscription_id=ci.course_subscription_id
 JOIN application_course c ON c.course_id=cs.course_id
 WHERE ci.payment_id = ANY(string_to_array($1, ',')::uuid[]) AND ($2::uuid IS NULL OR ci.group_id=$2::uuid)
 AND ($3::uuid IS NULL OR (c.sport_association_id=$3::uuid AND EXISTS(SELECT 1 FROM application_subscription s
 WHERE s.subscription_id=cs.subscription_id AND s.sport_association_id=$3::uuid)))
 ORDER BY ci.payment_id, ci.course_subscription_installment_id`, joined, group, scope)
	if err != nil {
		return nil, err
	}
	for _, row := range installments {
		if _, ok := result[row.PaymentID]; !ok {
			result[row.PaymentID] = domain.PaymentExportCourse{Found: true, Title: row.Title}
		}
	}
	return result, nil
}

func (r *PaymentExport) Transfers(ctx context.Context, association string, from, to time.Time) ([]domain.PaymentExportTransfer, error) {
	rows := []domain.PaymentExportTransfer{}
	err := r.db.SelectContext(ctx, &rows, `SELECT t.custom_account_transfer_id, t.amount::text AS amount, t.date,
 t.custom_account_from_id, af.name AS from_name, t.custom_account_to_id, dst.name AS to_name
 FROM application_customaccountstransfer t
 LEFT JOIN application_customaccounts af ON af.custom_account_id=t.custom_account_from_id
 LEFT JOIN application_customaccounts dst ON dst.custom_account_id=t.custom_account_to_id
 WHERE t.sport_association_id=$1 AND t.date BETWEEN $2 AND $3`, association, from, to)
	return rows, err
}

type paymentExportBalance struct {
	ID      string `db:"custom_account_id"`
	Balance string `db:"balance"`
}

// Balances is initial_balance + transfers to - transfers from + the live,
// group-aware paid payments (expenses negative), each within [1970-01-01, to].
func (r *PaymentExport) Balances(ctx context.Context, accounts []string, to time.Time, group *string) (map[string]string, error) {
	result := map[string]string{}
	if len(accounts) == 0 {
		return result, nil
	}
	rows := []paymentExportBalance{}
	err := r.db.SelectContext(ctx, &rows, `SELECT a.custom_account_id, (a.initial_balance
 + COALESCE((SELECT SUM(t.amount) FROM application_customaccountstransfer t WHERE t.custom_account_to_id=a.custom_account_id
 AND t.date BETWEEN $2 AND $3), 0)
 - COALESCE((SELECT SUM(t.amount) FROM application_customaccountstransfer t WHERE t.custom_account_from_id=a.custom_account_id
 AND t.date BETWEEN $2 AND $3), 0)
 + COALESCE((SELECT SUM(CASE WHEN p.expense THEN -p.amount ELSE p.amount END) FROM application_payment p
 WHERE p.custom_accounts_id=a.custom_account_id AND p.paid AND NOT p.deleted AND ($4::uuid IS NULL OR p.group_id=$4::uuid)
 AND p.payment_date BETWEEN $2 AND $3), 0))::text AS balance
 FROM application_customaccounts a WHERE a.custom_account_id = ANY(string_to_array($1, ',')::uuid[])`,
		strings.Join(accounts, ","), time.Date(1970, 1, 1, 0, 0, 0, 0, time.UTC), to, group)
	for _, row := range rows {
		result[row.ID] = row.Balance
	}
	return result, err
}

type paymentExportCategory struct {
	ID   string `db:"payment_category_id"`
	Name string `db:"name"`
}

func (r *PaymentExport) CategoryNames(ctx context.Context, association string) (map[string]string, error) {
	rows := []paymentExportCategory{}
	err := r.db.SelectContext(ctx, &rows, `SELECT payment_category_id::text AS payment_category_id, name FROM application_paymentcategory
 WHERE sport_association_id=$1 OR sport_association_id IS NULL`, association)
	result := make(map[string]string, len(rows))
	for _, row := range rows {
		result[row.ID] = row.Name
	}
	return result, err
}
