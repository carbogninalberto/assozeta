package repository

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// PrintingGenerate serves printing_generate's report builders
// (application/utils/printing.py). The builders' querysets have no ordering
// (except subscriptions_with_all_payments' -creation_date): the queries keep
// Django's joins and predicates so PostgreSQL answers them with the same plans,
// and the rows come back in the same database order.
type PrintingGenerate struct{ db *sqlx.DB }

func NewPrintingGenerate(db *sqlx.DB) *PrintingGenerate { return &PrintingGenerate{db: db} }

var _ domain.PrintingGenerateStore = (*PrintingGenerate)(nil)

type printingGenerateTransaction struct{ *ownedTransaction }

func (r *PrintingGenerate) BeginPrintingGenerate(ctx context.Context) (domain.PrintingGenerateTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &printingGenerateTransaction{ownedTransaction: tx}, nil
}

// Association is SportAssociation.objects (ActiveManager: live rows)
// .get(user=...): no row or several rows raise.
func (t *printingGenerateTransaction) Association(ctx context.Context, user string) (*domain.PrintingAssociation, error) {
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

const printingPersonColumns = `%[1]s.associate_id, %[1]s.sport_association_id AS associate_association, %[1]s.first_name, %[1]s.last_name,
 %[1]s.email, %[1]s.phone, %[1]s.born_date`

func printingPerson(alias string) string { return fmt.Sprintf(printingPersonColumns, alias) }

// printingSubscriptionWhere is filter_subscriptions' WHERE clause over the
// subscription alias s, with its arguments appended to args.
func printingSubscriptionWhere(alias string, filter domain.PrintingSubscriptionFilter, args *[]any) string {
	arg := func(value any) string {
		*args = append(*args, value)
		return "$" + strconv.Itoa(len(*args))
	}
	where := []string{alias + ".sport_association_id = " + arg(filter.Association) + "::uuid", "NOT " + alias + ".archived"}
	if filter.Group != nil {
		where = append(where, alias+".group_id = "+arg(*filter.Group)+"::uuid")
	}
	if filter.OnlyMembers {
		where = append(where, alias+".type IN (3, 2)")
	}
	if filter.Medical {
		where = append(where, alias+".medical_id IS NOT NULL")
	}
	today := filter.Today.Format("2006-01-02")
	switch filter.Year {
	case "pre-iscrizioni":
		where = append(where, alias+".start_date > "+arg(today)+"::date")
	case "corrente":
		where = append(where, alias+".start_date <= "+arg(today)+"::date", alias+".end_date >= "+arg(today)+"::date")
	case "precedenti":
		where = append(where, alias+".end_date < "+arg(today)+"::date")
	}
	if filter.StatusFilter {
		values := make([]string, len(filter.Statuses))
		for i, status := range filter.Statuses {
			values[i] = strconv.FormatInt(status, 10)
		}
		if len(values) == 0 {
			where = append(where, "false")
		} else {
			where = append(where, alias+".status_flag IN ("+strings.Join(values, ", ")+")")
		}
	}
	return strings.Join(where, " AND ")
}

// Subscriptions is filter_subscriptions(...).select_related('medical', 'associate').
func (t *printingGenerateTransaction) Subscriptions(ctx context.Context, filter domain.PrintingSubscriptionFilter) ([]domain.PrintingSubscription, error) {
	args := []any{}
	where := printingSubscriptionWhere("s", filter, &args)
	rows := []domain.PrintingSubscription{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT s.subscription_id, s.type, s.end_date, s.medical_id IS NOT NULL AS has_medical,
 m.expiration_date AS medical_expiration, `+printingPerson("a")+`
 FROM application_subscription s LEFT OUTER JOIN application_medicalcertificate m ON s.medical_id = m.medical_id
 LEFT OUTER JOIN application_associate a ON s.associate_id = a.associate_id WHERE `+where, args...)
	return rows, err
}

// Relations are the associates' tutor relations (by primary key) with their
// tutors through the base manager.
func (t *printingGenerateTransaction) Relations(ctx context.Context, associates []string) ([]domain.PrintingRelation, error) {
	rows := []domain.PrintingRelation{}
	if len(associates) == 0 {
		return rows, nil
	}
	err := t.tx.SelectContext(ctx, &rows, `SELECT r.id, r.associate_id AS relation_associate_id, r.is_primary, `+printingPerson("p")+`
 FROM application_associatetutorrelation r JOIN application_associate p ON p.associate_id=r.tutor_id
 WHERE r.associate_id = ANY($1::uuid[]) ORDER BY r.id`, uuidArray(associates))
	return rows, err
}

// Memberships is SubscriptionMembership.objects.filter(associate__in=
// <the subscriptions' associate values>, end_date__isnull=False,
// end_date__lte=horizon).select_related('associate').
func (t *printingGenerateTransaction) Memberships(ctx context.Context, filter domain.PrintingSubscriptionFilter, horizon time.Time) ([]domain.PrintingMembership, error) {
	args := []any{}
	where := printingSubscriptionWhere("u0", filter, &args)
	args = append(args, horizon.Format("2006-01-02"))
	rows := []domain.PrintingMembership{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT sm.membership_type, sm.end_date, `+printingPerson("a")+`
 FROM application_subscriptionmembership sm LEFT OUTER JOIN application_associate a ON sm.associate_id = a.associate_id
 WHERE sm.associate_id IN (SELECT u0.associate_id FROM application_subscription u0 WHERE `+where+`)
 AND sm.end_date IS NOT NULL AND sm.end_date <= $`+strconv.Itoa(len(args))+`::date`, args...)
	return rows, err
}

// printingPaymentColumns reads the related rows the Python attribute
// accesses fetch lazily (payment_category, invoice) as scalar subqueries, so
// the report query keeps Django's joins.
const printingPaymentColumns = `p.payment_id, p.subject, p.amount::text AS amount, p.creation_date, p.payment_date, p.paid, p.description,
 p.meta::text AS meta, p.course::text AS course, p.payment_category_id IS NOT NULL AS has_category,
 (SELECT c.name FROM application_paymentcategory c WHERE c.payment_category_id = p.payment_category_id) AS category_name,
 p.custom_accounts_id IS NOT NULL AS has_account, p.invoice_id IS NOT NULL AS has_invoice`

const printingInvoiceNumber = `(SELECT i.number FROM application_invoice i WHERE i.invoice_id = p.invoice_id) AS invoice_number`

// Payments is the payment queryset of not_paid_quotes_subscriptions
// ("quotes"), not_paid_courses_subscriptions ("courses") and expired_payments
// ("expired") over the group-aware live Payment.objects.
func (t *printingGenerateTransaction) Payments(ctx context.Context, query domain.PrintingPaymentQuery) ([]domain.PrintingPayment, error) {
	rows := []domain.PrintingPayment{}
	if len(query.Associates) == 0 {
		return rows, nil // associate__in=[]: EmptyResultSet
	}
	args := []any{query.Association, uuidArray(query.Associates), query.Now}
	where := []string{"NOT p.deleted", "p.sport_association_id = $1::uuid", "p.associate_id = ANY($2::uuid[])"}
	if query.Group != nil {
		args = append(args, *query.Group)
		where = append(where, "p.group_id = $"+strconv.Itoa(len(args))+"::uuid")
	}
	joined := true
	switch query.Kind {
	case "quotes":
		where = append(where, "p.subject = 1", "NOT p.paid", "p.creation_date < $3")
	case "courses":
		where = append(where, "p.subject = 2", "NOT p.paid", "p.amount > 0", "p.creation_date < $3")
	case "expired":
		where = append(where, "NOT p.paid", "NOT p.archived", "p.associate_id IS NOT NULL", "NOT p.expense", "p.amount > 0",
			"p.creation_date <= $3")
		joined = query.Period // select_related('associate') only inside the period branch
	}
	if query.Period {
		args = append(args, query.From, query.To)
		where = append(where, "p.creation_date >= $"+strconv.Itoa(len(args)-1), "p.creation_date <= $"+strconv.Itoa(len(args)))
	}
	from, person := ` FROM application_payment p`, printingPersonLookup
	if joined {
		from += ` LEFT OUTER JOIN application_associate a ON p.associate_id = a.associate_id`
		person = printingPerson("a")
	}
	err := t.tx.SelectContext(ctx, &rows, `SELECT `+printingPaymentColumns+`, `+printingInvoiceNumber+`, `+person+from+
		` WHERE `+strings.Join(where, " AND "), args...)
	return rows, err
}

// printingPersonLookup reads the payment's associate per row (the lazy
// p.associate access) without joining it into the report query.
const printingPersonLookup = `p.associate_id,
 (SELECT x.sport_association_id FROM application_associate x WHERE x.associate_id = p.associate_id) AS associate_association,
 (SELECT x.first_name FROM application_associate x WHERE x.associate_id = p.associate_id) AS first_name,
 (SELECT x.last_name FROM application_associate x WHERE x.associate_id = p.associate_id) AS last_name,
 (SELECT x.email FROM application_associate x WHERE x.associate_id = p.associate_id) AS email,
 (SELECT x.phone FROM application_associate x WHERE x.associate_id = p.associate_id) AS phone,
 (SELECT x.born_date FROM application_associate x WHERE x.associate_id = p.associate_id) AS born_date`

// AssociatePayments is subscriptions_with_all_payments' Payment.objects
// .filter(associate=..., sport_association, archived=False, expense=False)
// .select_related('invoice').order_by('-creation_date') (+ the period).
func (t *printingGenerateTransaction) AssociatePayments(ctx context.Context, associate *string, association string, group *string, period bool,
	from, to time.Time) ([]domain.PrintingPayment, error) {
	args := []any{association}
	where := []string{"NOT p.deleted", "p.sport_association_id = $1::uuid", "NOT p.archived", "NOT p.expense"}
	if associate == nil {
		where = append(where, "p.associate_id IS NULL")
	} else {
		args = append(args, *associate)
		where = append(where, "p.associate_id = $"+strconv.Itoa(len(args))+"::uuid")
	}
	if group != nil {
		args = append(args, *group)
		where = append(where, "p.group_id = $"+strconv.Itoa(len(args))+"::uuid")
	}
	if period {
		args = append(args, from, to)
		where = append(where, "p.creation_date >= $"+strconv.Itoa(len(args)-1), "p.creation_date <= $"+strconv.Itoa(len(args)))
	}
	rows := []domain.PrintingPayment{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT `+printingPaymentColumns+`, i.number AS invoice_number FROM application_payment p
 LEFT OUTER JOIN application_invoice i ON p.invoice_id = i.invoice_id WHERE `+strings.Join(where, " AND ")+
		` ORDER BY p.creation_date DESC`, args...)
	return rows, err
}

// PaymentLink is get_course_carnet_name's lookups: the first course
// subscription (all_objects), else the first group-aware
// installment (all_objects), else the first carnet
// subscription paid by the payment, each by primary key.
func (t *printingGenerateTransaction) PaymentLink(ctx context.Context, payment string, group *string) (domain.PrintingPaymentLink, error) {
	lookups := []struct {
		query string
		args  []any
	}{
		{`SELECT c.title, false AS missing FROM application_coursesubscription s JOIN application_course c ON c.course_id=s.course_id
 WHERE s.payment_id=$1 ORDER BY s.course_subscription_id LIMIT 1`, []any{payment}},
		{`SELECT c.title, false AS missing FROM application_coursesubscriptioninstallment i
 JOIN application_coursesubscription s ON s.course_subscription_id=i.course_subscription_id
 JOIN application_course c ON c.course_id=s.course_id WHERE i.payment_id=$1 AND ($2::uuid IS NULL OR i.group_id=$2::uuid)
 ORDER BY i.course_subscription_installment_id LIMIT 1`, []any{payment, group}},
		{`SELECT k.title, k.carnet_id IS NULL AS missing FROM application_carnetsubscription s
 LEFT JOIN application_carnet k ON k.carnet_id=s.carnet_id_id WHERE s.payment_id=$1 ORDER BY s.carnet_subscription_id LIMIT 1`,
			[]any{payment}},
	}
	for slot, lookup := range lookups {
		var row struct {
			Title   *string `db:"title"`
			Missing bool    `db:"missing"`
		}
		err := t.tx.GetContext(ctx, &row, lookup.query, lookup.args...)
		if errors.Is(err, sql.ErrNoRows) {
			continue
		}
		if err != nil {
			return domain.PrintingPaymentLink{}, err
		}
		return domain.PrintingPaymentLink{Slot: slot, Title: row.Title, MissingCarnet: row.Missing}, nil
	}
	return domain.PrintingPaymentLink{Slot: -1}, nil
}

// PromoteRelation is get_main_tutor's tutor_relation.save() with
// is_primary=True and its auditlog update entry.
func (t *printingGenerateTransaction) PromoteRelation(ctx context.Context, promotion domain.PrintingPromotion) error {
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_associatetutorrelation SET is_primary=true WHERE id=$1`, promotion.RelationID); err != nil {
		return err
	}
	key := strconv.FormatInt(promotion.RelationID, 10)
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "associatetutorrelation", Key: key,
		Representation: "AssociateTutorRelation object (" + key + ")", Action: 1,
		Changes: map[string][2]string{"is_primary": {"False", "True"}}, Context: promotion.Context,
		AssociationID: promotion.AssociationID, ResolutionPath: promotion.Path, At: promotion.At})
}

func (t *printingGenerateTransaction) ActiveAssociationOf(ctx context.Context, user string) (*string, error) {
	var id string
	err := t.tx.GetContext(ctx, &id, `SELECT sport_association_id FROM application_sportassociation
 WHERE user_id=$1 AND NOT deleted ORDER BY sport_association_id LIMIT 1`, user)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &id, err
}
