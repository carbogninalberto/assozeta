package repository

import (
	"context"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// PaymentReadsAthlete reads payment_list's athlete branch.
type PaymentReadsAthlete struct{ db *sqlx.DB }

func NewPaymentReadsAthlete(db *sqlx.DB) *PaymentReadsAthlete { return &PaymentReadsAthlete{db: db} }

var _ domain.PaymentAthleteStore = (*PaymentReadsAthlete)(nil)

// paymentAthleteQuery is the statement prefix and scoped_queryset's filters of
// an association-scoped athlete session ("" outside one). Parameters:
// $1 association, $2 athlete, $3 group, then the caller's.
type paymentAthleteQuery struct {
	prefix string
	// payment filters application_payment p; subscription application_subscription s;
	// courseSubscription a course subscription cs joined to its subscription su and
	// course co (also an installment's); holding a carnet holding h joined to its
	// subscription su and carnet k.
	payment, subscription, courseSubscription, holding string
}

func paymentAthleteScoped(scope domain.PaymentAthleteScope) paymentAthleteQuery {
	query := paymentAthleteQuery{prefix: `WITH scope_parameters AS (SELECT $1::uuid association_id, $2::uuid effective_id, $3::uuid group_id)`}
	if scope.Association == nil {
		return query
	}
	query.prefix += familyScopeCTE
	query.payment = ` AND p.sport_association_id=$1 AND p.payment_id IN (SELECT payment_id FROM visible_payments)`
	query.subscription = ` AND s.sport_association_id=$1 AND s.subscription_id IN (SELECT subscription_id FROM visible_subscriptions)`
	query.courseSubscription = ` AND su.sport_association_id=$1 AND co.sport_association_id=$1
  AND cs.subscription_id IN (SELECT subscription_id FROM visible_subscriptions)`
	query.holding = ` AND su.sport_association_id=$1 AND k.sport_association_id=$1
  AND h.subscription_id IN (SELECT subscription_id FROM visible_subscriptions)`
	return query
}

func paymentAthleteArgs(scope domain.PaymentAthleteScope, args ...any) []any {
	return append([]any{scope.Association, scope.Person, scope.Group}, args...)
}

func (r *PaymentReadsAthlete) CourseSubscription(ctx context.Context, id string, scope domain.PaymentAthleteScope) (*domain.PaymentAthleteCourseSubscription, error) {
	query := paymentAthleteScoped(scope)
	rows := []domain.PaymentAthleteCourseSubscription{}
	err := r.db.SelectContext(ctx, &rows, query.prefix+`
 SELECT cs.course_subscription_id, cs.multi_payments, cs.payment_id, pp.amount > 0 AS payment_positive, ou.show_zero_payments AS show_zero
 FROM application_coursesubscription cs
 JOIN application_course co ON co.course_id=cs.course_id
 LEFT JOIN application_subscription su ON su.subscription_id=cs.subscription_id
 LEFT JOIN application_payment pp ON pp.payment_id=cs.payment_id
 LEFT JOIN application_sportassociation oa ON oa.sport_association_id=co.sport_association_id
 LEFT JOIN bakney_user ou ON ou.user_id=oa.user_id
 WHERE cs.course_subscription_id=$4::uuid AND NOT cs.deleted`+query.courseSubscription+`
 ORDER BY cs.course_subscription_id LIMIT 1`, paymentAthleteArgs(scope, id)...)
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	return &rows[0], nil
}

func (r *PaymentReadsAthlete) InstallmentCandidates(ctx context.Context, courseSubscription string, scope domain.PaymentAthleteScope) ([]domain.PaymentAthleteCandidate, error) {
	query := paymentAthleteScoped(scope)
	rows := []domain.PaymentAthleteCandidate{}
	err := r.db.SelectContext(ctx, &rows, query.prefix+`
 SELECT i.payment_id, i.amount > 0 AS positive, NULL::boolean AS show_zero
 FROM application_coursesubscriptioninstallment i
 JOIN application_coursesubscription cs ON cs.course_subscription_id=i.course_subscription_id
 JOIN application_course co ON co.course_id=cs.course_id
 LEFT JOIN application_subscription su ON su.subscription_id=cs.subscription_id
 WHERE i.course_subscription_id=$4::uuid AND NOT i.deleted AND ($3::uuid IS NULL OR i.group_id=$3)`+query.courseSubscription,
		paymentAthleteArgs(scope, courseSubscription)...)
	return rows, err
}

func (r *PaymentReadsAthlete) CarnetCandidates(ctx context.Context, courseSubscription string, scope domain.PaymentAthleteScope) ([]domain.PaymentAthleteCandidate, error) {
	query := paymentAthleteScoped(scope)
	rows := []domain.PaymentAthleteCandidate{}
	err := r.db.SelectContext(ctx, &rows, query.prefix+`
 SELECT h.payment_id, hp.amount > 0 AS positive, ku.show_zero_payments AS show_zero
 FROM application_carnetsubscription h
 JOIN application_carnetsubscription_course_subscription l ON l.carnetsubscription_id=h.carnet_subscription_id
 LEFT JOIN application_carnet k ON k.carnet_id=h.carnet_id_id
 LEFT JOIN application_sportassociation ka ON ka.sport_association_id=k.sport_association_id
 LEFT JOIN bakney_user ku ON ku.user_id=ka.user_id
 LEFT JOIN application_payment hp ON hp.payment_id=h.payment_id
 LEFT JOIN application_subscription su ON su.subscription_id=h.subscription_id
 WHERE l.coursesubscription_id=$4::uuid`+query.holding, paymentAthleteArgs(scope, courseSubscription)...)
	return rows, err
}

// paymentAthleteRows selects a payment with its associate, its invoice's PDF
// and its association's denomination and owner.
const paymentAthleteRows = `
 SELECT p.payment_id, p.type, p.subject, p.amount::text AS amount, p.description, p.paid, p.creation_date, p.payment_date,
  p.payment_category_id, p.sport_association_id, p.notes, p.course::text AS course, p.associate_id,
  pa.first_name AS associate_first_name, pa.last_name AS associate_last_name, p.invoice_id, pv.document_pdf_id AS invoice_document,
  psa.denomination, pu.online_payments AS owner_online
 FROM application_payment p
 LEFT JOIN application_associate pa ON pa.associate_id=p.associate_id
 LEFT JOIN application_invoice pv ON pv.invoice_id=p.invoice_id
 LEFT JOIN application_sportassociation psa ON psa.sport_association_id=p.sport_association_id
 LEFT JOIN bakney_user pu ON pu.user_id=psa.user_id
 WHERE NOT p.deleted AND ($3::uuid IS NULL OR p.group_id=$3)`

func (r *PaymentReadsAthlete) ByIDs(ctx context.Context, ids []string, scope domain.PaymentAthleteScope) ([]domain.PaymentAthleteRow, error) {
	rows := []domain.PaymentAthleteRow{}
	if len(ids) == 0 {
		return rows, nil
	}
	query := paymentAthleteScoped(scope)
	err := r.db.SelectContext(ctx, &rows, query.prefix+paymentAthleteRows+`
  AND p.payment_id = ANY(string_to_array($4, ',')::uuid[])`+query.payment, paymentAthleteArgs(scope, strings.Join(ids, ","))...)
	return rows, err
}

func (r *PaymentReadsAthlete) Listed(ctx context.Context, scope domain.PaymentAthleteScope, limit time.Time) ([]domain.PaymentAthleteRow, error) {
	query := paymentAthleteScoped(scope)
	rows := []domain.PaymentAthleteRow{}
	err := r.db.SelectContext(ctx, &rows, query.prefix+paymentAthleteRows+`
  AND (p.user_id=$2 OR p.associate_id IN (SELECT s.associate_id FROM application_subscription s
   WHERE s.user_id=$2 AND ($3::uuid IS NULL OR s.group_id=$3) AND s.associate_id IS NOT NULL`+query.subscription+`))
  AND p.amount >= 0 AND NOT p.expense AND p.creation_date <= $4`+query.payment+`
 ORDER BY p.paid, p.creation_date DESC`, paymentAthleteArgs(scope, limit)...)
	return rows, err
}

func (r *PaymentReadsAthlete) Exclusions(ctx context.Context, scope domain.PaymentAthleteScope, today time.Time) ([]domain.PaymentAthleteExclusion, error) {
	query := paymentAthleteScoped(scope)
	course := ""
	if scope.Association != nil {
		course = ` AND co.sport_association_id=$1`
	}
	rows := []domain.PaymentAthleteExclusion{}
	err := r.db.SelectContext(ctx, &rows, query.prefix+`
 SELECT co.multi_payments AS multi, cs.payment_id AS course_payment, cp.amount = 0 AS course_zero,
  i.course_subscription_installment_id IS NOT NULL AS has_installment, i.payment_id AS installment_payment,
  ip.amount = 0 AS installment_zero, ((i.payment_date AT TIME ZONE 'UTC')::date - $4::date) > 31 AS late,
  ou.show_zero_payments AS show_zero
 FROM application_subscription s
 JOIN application_sportassociation sa ON sa.sport_association_id=s.sport_association_id
 JOIN bakney_user ou ON ou.user_id=sa.user_id
 JOIN application_coursesubscription cs ON cs.subscription_id=s.subscription_id AND NOT cs.deleted
 JOIN application_course co ON co.course_id=cs.course_id
 LEFT JOIN application_payment cp ON cp.payment_id=cs.payment_id
 LEFT JOIN application_coursesubscriptioninstallment i ON co.multi_payments AND i.course_subscription_id=cs.course_subscription_id
  AND NOT i.deleted AND NOT i.paid AND ($3::uuid IS NULL OR i.group_id=$3)
 LEFT JOIN application_payment ip ON ip.payment_id=i.payment_id
 WHERE s.user_id=$2 AND ($3::uuid IS NULL OR s.group_id=$3)`+query.subscription+course,
		paymentAthleteArgs(scope, today.Format("2006-01-02"))...)
	return rows, err
}

func (r *PaymentReadsAthlete) Installments(ctx context.Context, payments []string, scope domain.PaymentAthleteScope) ([]domain.PaymentAthleteInstallment, error) {
	rows := []domain.PaymentAthleteInstallment{}
	if len(payments) == 0 {
		return rows, nil
	}
	query := paymentAthleteScoped(scope)
	err := r.db.SelectContext(ctx, &rows, query.prefix+`
 SELECT i.payment_id, i.payment_date, co.one_fee_payment, ip.paid AS payment_paid, co.events::text AS events
 FROM application_coursesubscriptioninstallment i
 JOIN application_coursesubscription cs ON cs.course_subscription_id=i.course_subscription_id
 JOIN application_course co ON co.course_id=cs.course_id
 LEFT JOIN application_subscription su ON su.subscription_id=cs.subscription_id
 LEFT JOIN application_payment ip ON ip.payment_id=i.payment_id
 WHERE i.payment_id = ANY(string_to_array($4, ',')::uuid[]) AND NOT i.deleted AND ($3::uuid IS NULL OR i.group_id=$3)`+
		query.courseSubscription, paymentAthleteArgs(scope, strings.Join(payments, ","))...)
	return rows, err
}

// paymentAthleteLink is the first row of one of get_meta's lookups.
type paymentAthleteLink struct {
	Number       *int64     `db:"number"`
	Date         *time.Time `db:"payment_date"`
	Name         *string    `db:"name"`
	HasNamed     bool       `db:"has_named"`
	Subscription *string    `db:"subscription_id"`
	Start        *string    `db:"start_date"`
	End          *string    `db:"end_date"`
}

func (r *PaymentReadsAthlete) Meta(ctx context.Context, payment string, group *string) (domain.PaymentAthleteMeta, error) {
	lookups := []struct {
		kind  string
		query string
		args  []any
	}{
		// CourseSubscriptionInstallment.objects (group-aware, live).
		{domain.PaymentMetaInstallment, `SELECT i.id AS number, i.payment_date, co.title AS name, TRUE AS has_named, cs.subscription_id,
  su.start_date::text AS start_date, su.end_date::text AS end_date
 FROM application_coursesubscriptioninstallment i
 JOIN application_coursesubscription cs ON cs.course_subscription_id=i.course_subscription_id
 JOIN application_course co ON co.course_id=cs.course_id
 LEFT JOIN application_subscription su ON su.subscription_id=cs.subscription_id
 WHERE i.payment_id=$1::uuid AND NOT i.deleted AND ($2::uuid IS NULL OR i.group_id=$2)
 ORDER BY i.course_subscription_installment_id LIMIT 1`, []any{payment, group}},
		// Subscription.objects (group-aware).
		{domain.PaymentMetaSubscription, `SELECT NULL::bigint AS number, NULL::timestamptz AS payment_date, sa.denomination AS name, TRUE AS has_named,
  su.subscription_id, su.start_date::text AS start_date, su.end_date::text AS end_date
 FROM application_subscription su JOIN application_sportassociation sa ON sa.sport_association_id=su.sport_association_id
 WHERE su.payment_id=$1::uuid AND ($2::uuid IS NULL OR su.group_id=$2)
 ORDER BY su.subscription_id LIMIT 1`, []any{payment, group}},
		{domain.PaymentMetaCarnet, `SELECT NULL::bigint AS number, NULL::timestamptz AS payment_date, k.title AS name, k.carnet_id IS NOT NULL AS has_named,
  h.subscription_id, su.start_date::text AS start_date, su.end_date::text AS end_date
 FROM application_carnetsubscription h LEFT JOIN application_carnet k ON k.carnet_id=h.carnet_id_id
 LEFT JOIN application_subscription su ON su.subscription_id=h.subscription_id
 WHERE h.payment_id=$1::uuid ORDER BY h.carnet_subscription_id LIMIT 1`, []any{payment}},
		// CourseSubscription.objects (live).
		{domain.PaymentMetaCourse, `SELECT NULL::bigint AS number, NULL::timestamptz AS payment_date, co.title AS name, TRUE AS has_named,
  cs.subscription_id, su.start_date::text AS start_date, su.end_date::text AS end_date
 FROM application_coursesubscription cs JOIN application_course co ON co.course_id=cs.course_id
 LEFT JOIN application_subscription su ON su.subscription_id=cs.subscription_id
 WHERE cs.payment_id=$1::uuid AND NOT cs.deleted ORDER BY cs.course_subscription_id LIMIT 1`, []any{payment}},
		{domain.PaymentMetaCamp, `SELECT NULL::bigint AS number, NULL::timestamptz AS payment_date, cp.title AS name, TRUE AS has_named,
  cu.subscription_id, su.start_date::text AS start_date, su.end_date::text AS end_date
 FROM application_campsandretreatssubscriptionperiod l
 JOIN application_campsandretreatsperiod cp ON cp.camps_and_retreats_period_id=l.camps_and_retreats_period_id
 JOIN application_campsandretreatssubscription cu ON cu.camps_and_retreats_subscription_id=l.camps_and_retreats_subscription_id
 LEFT JOIN application_subscription su ON su.subscription_id=cu.subscription_id
 WHERE l.payment_id=$1::uuid ORDER BY l.camps_and_retreats_subscription_period_id LIMIT 1`, []any{payment}},
	}
	for _, lookup := range lookups {
		rows := []paymentAthleteLink{}
		if err := r.db.SelectContext(ctx, &rows, lookup.query, lookup.args...); err != nil {
			return domain.PaymentAthleteMeta{}, err
		}
		if len(rows) == 0 {
			continue
		}
		row := rows[0]
		if !row.HasNamed || row.Subscription == nil {
			// A related carnet or subscription that is None raises AttributeError.
			return domain.PaymentAthleteMeta{}, domain.ErrPaymentReadsServer
		}
		return domain.PaymentAthleteMeta{Kind: lookup.kind, Number: row.Number, Date: row.Date, Name: row.Name,
			SubscriptionID: *row.Subscription, Start: row.Start, End: row.End}, nil
	}
	return domain.PaymentAthleteMeta{}, nil
}
