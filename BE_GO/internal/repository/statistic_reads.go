package repository

import (
	"context"
	"database/sql"
	"errors"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// StatisticReads backs statistic/dashboard, statistic/report and
// statistic/athlete-dashboard. Managers are reproduced per model: Subscription
// is group-aware only; Payment, Course, Invoice and Associate are group-aware
// and hide soft-deleted rows; CourseSubscription hides soft-deleted rows.
// Related rows reached through joins never apply a manager.
type StatisticReads struct{ db *sqlx.DB }

func NewStatisticReads(db *sqlx.DB) *StatisticReads { return &StatisticReads{db: db} }

var (
	_ domain.StatisticDashboardStore = (*StatisticReads)(nil)
	_ domain.StatisticReportStore    = (*StatisticReads)(nil)
	_ domain.StatisticAthleteStore   = (*StatisticReads)(nil)
)

// $1 association, $2 group (NULL: no X-Group-Id filter).
const statisticGroup = `($2::uuid IS NULL OR group_id=$2::uuid)`

func (r *StatisticReads) ScopedAssociation(ctx context.Context, user string, scope *string) (string, error) {
	var ids []string
	err := r.db.SelectContext(ctx, &ids, `SELECT sport_association_id FROM application_sportassociation
 WHERE user_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR sport_association_id=$2::uuid) LIMIT 2`, user, scope)
	if err != nil {
		return "", err
	}
	if len(ids) != 1 {
		return "", errors.Join(domain.ErrAssociationNotFound, sql.ErrNoRows)
	}
	return ids[0], nil
}

func (r *StatisticReads) Balance(ctx context.Context, user string) (domain.StatisticBalance, error) {
	var balance domain.StatisticBalance
	err := r.db.GetContext(ctx, &balance, `SELECT balance_sheet_start_day, balance_sheet_start_month FROM bakney_user WHERE user_id=$1`, user)
	return balance, err
}

// --- statistic/dashboard ----------------------------------------------------------------------

func (r *StatisticReads) SubscriptionStatuses(ctx context.Context, scope domain.StatisticScope, today string) ([]domain.StatisticKeyCount, error) {
	rows := []domain.StatisticKeyCount{}
	err := r.db.SelectContext(ctx, &rows, `SELECT status_flag AS key, count(*) AS count FROM application_subscription
 WHERE sport_association_id=$1 AND `+statisticGroup+` AND status_flag IN (1,2,3,4) AND NOT archived AND type IN (2,3)
 AND start_date <= $3::date AND end_date >= $3::date GROUP BY status_flag`, scope.Association, scope.Group, today)
	return rows, err
}

func (r *StatisticReads) DailyPayments(ctx context.Context, scope domain.StatisticScope, from, to time.Time) ([]domain.StatisticDaily, error) {
	rows := []domain.StatisticDaily{}
	err := r.db.SelectContext(ctx, &rows, `SELECT (creation_date AT TIME ZONE 'UTC')::date AS day, sum(amount)::text AS value
 FROM application_payment WHERE NOT deleted AND `+statisticGroup+` AND amount > 0 AND paid AND sport_association_id=$1
 AND creation_date BETWEEN $3 AND $4 GROUP BY 1`, scope.Association, scope.Group, from, to)
	return rows, err
}

func (r *StatisticReads) DailySubscriptions(ctx context.Context, scope domain.StatisticScope, from, to time.Time) ([]domain.StatisticDaily, error) {
	rows := []domain.StatisticDaily{}
	err := r.db.SelectContext(ctx, &rows, `SELECT (creation_date AT TIME ZONE 'UTC')::date AS day, count(*)::text AS value
 FROM application_subscription WHERE sport_association_id=$1 AND `+statisticGroup+` AND creation_date BETWEEN $3 AND $4 GROUP BY 1`,
		scope.Association, scope.Group, from, to)
	return rows, err
}

func (r *StatisticReads) BestCourses(ctx context.Context, scope domain.StatisticScope) ([]domain.StatisticBestCourse, error) {
	rows := []domain.StatisticBestCourse{}
	// Count('coursesubscription') joins every course subscription, soft-deleted ones included.
	err := r.db.SelectContext(ctx, &rows, `SELECT c.course_id, c.title, c.description, count(cs.course_subscription_id) AS subscriptions
 FROM application_course c LEFT JOIN application_coursesubscription cs ON cs.course_id=c.course_id
 WHERE NOT c.deleted AND ($2::uuid IS NULL OR c.group_id=$2::uuid) AND c.sport_association_id=$1 AND c.status_flag=2
 GROUP BY c.course_id ORDER BY subscriptions DESC LIMIT 3`, scope.Association, scope.Group)
	return rows, err
}

// scoped_queryset for CourseSubscription during impersonation: the subscription
// and the course must both belong to the impersonated association ($2 here).
const statisticScopedCourseSubscription = `($2::uuid IS NULL OR (cs.subscription_id IN (SELECT subscription_id FROM application_subscription
 WHERE sport_association_id=$2::uuid) AND c.sport_association_id=$2::uuid))`

func (r *StatisticReads) DailyCourseSubscriptions(ctx context.Context, scope domain.StatisticScope, from, to time.Time) ([]domain.StatisticDaily, error) {
	rows := []domain.StatisticDaily{}
	err := r.db.SelectContext(ctx, &rows, `SELECT (cs.creation_date AT TIME ZONE 'UTC')::date AS day, count(*)::text AS value
 FROM application_coursesubscription cs JOIN application_course c ON c.course_id=cs.course_id
 WHERE NOT cs.deleted AND c.sport_association_id=$1 AND `+statisticScopedCourseSubscription+` AND cs.creation_date BETWEEN $3 AND $4
 GROUP BY 1`, scope.Association, scope.Impersonation, from, to)
	return rows, err
}

func (r *StatisticReads) CourseSubscriptionCount(ctx context.Context, scope domain.StatisticScope) (int64, error) {
	var count int64
	err := r.db.GetContext(ctx, &count, `SELECT count(*) FROM application_coursesubscription cs JOIN application_course c ON c.course_id=cs.course_id
 WHERE NOT cs.deleted AND c.sport_association_id=$1 AND `+statisticScopedCourseSubscription, scope.Association, scope.Impersonation)
	return count, err
}

func (r *StatisticReads) ExpiringCarnets(ctx context.Context, scope domain.StatisticScope, owner string) ([]domain.StatisticCarnet, error) {
	rows := []domain.StatisticCarnet{}
	// The PostgreSQL branch: jsonb ordering (null < string < number < boolean)
	// keeps every non-boolean scalar below 4; lessons_left = 0 needs a registry.
	err := r.db.SelectContext(ctx, &rows, `SELECT cs.carnet_subscription_id, cs.creation_date, cs.meta::text AS meta, s.subscription_id,
 a.associate_id, a.first_name, a.last_name, k.carnet_id, k.title, k.description, k.lessons_number, k.fee::text AS fee, k.public
 FROM application_carnetsubscription cs JOIN application_subscription s ON s.subscription_id=cs.subscription_id
 JOIN application_sportassociation sa ON sa.sport_association_id=s.sport_association_id
 LEFT JOIN application_associate a ON a.associate_id=s.associate_id LEFT JOIN application_carnet k ON k.carnet_id=cs.carnet_id_id
 WHERE sa.user_id=$1 AND cs.meta ? 'lessons_left' AND (cs.meta -> 'lessons_left') < '4'::jsonb
 AND NOT ((cs.meta -> 'lessons_left') = '0'::jsonb AND NOT (cs.meta ? 'lessons_registry'))
 AND ($2::uuid IS NULL OR (s.sport_association_id=$2::uuid AND k.sport_association_id=$2::uuid))`, owner, scope.Impersonation)
	return rows, err
}

func (r *StatisticReads) Medicals(ctx context.Context, scope domain.StatisticScope, today string, expiring bool, year domain.StatisticYear) ([]domain.StatisticMedical, error) {
	window := `m.expiration_date < $3::date`
	if expiring {
		window = `NOT s.archived AND m.expiration_date <= $3::date + 30 AND m.expiration_date >= $3::date`
	}
	rows := []domain.StatisticMedical{}
	err := r.db.SelectContext(ctx, &rows, `SELECT s.subscription_id, m.expiration_date, a.associate_id, a.first_name, a.last_name
 FROM application_subscription s JOIN application_medicalcertificate m ON m.medical_id=s.medical_id
 LEFT JOIN application_associate a ON a.associate_id=s.associate_id
 WHERE s.sport_association_id=$1 AND ($2::uuid IS NULL OR s.group_id=$2::uuid) AND m.expiration_date IS NOT NULL AND `+window+`
 AND s.creation_date BETWEEN $4 AND $5`, scope.Association, scope.Group, today, year.From, year.To)
	return rows, err
}

func (r *StatisticReads) Approvals(ctx context.Context, scope domain.StatisticScope, today string) ([]domain.StatisticApproval, error) {
	rows := []domain.StatisticApproval{}
	err := r.db.SelectContext(ctx, &rows, `SELECT s.creation_date, s.subscription_id, s.status_flag, a.associate_id, a.first_name, a.last_name
 FROM application_subscription s LEFT JOIN application_associate a ON a.associate_id=s.associate_id
 WHERE s.sport_association_id=$1 AND ($2::uuid IS NULL OR s.group_id=$2::uuid) AND s.status_flag IN (1,2) AND NOT s.archived
 AND s.start_date <= $3::date AND s.end_date >= $3::date ORDER BY s.creation_date DESC, s.status_flag DESC LIMIT 100`,
		scope.Association, scope.Group, today)
	return rows, err
}

func (r *StatisticReads) IncomeAndExpenses(ctx context.Context, scope domain.StatisticScope, from, to time.Time) (domain.StatisticSums, error) {
	var sums domain.StatisticSums
	err := r.db.GetContext(ctx, &sums, `SELECT (sum(amount) FILTER (WHERE NOT expense))::text AS income,
 (sum(amount) FILTER (WHERE expense))::text AS expenses FROM application_payment
 WHERE NOT deleted AND `+statisticGroup+` AND sport_association_id=$1 AND NOT archived AND payment_date IS NOT NULL AND paid
 AND payment_date BETWEEN $3 AND $4`, scope.Association, scope.Group, from, to)
	return sums, err
}

// --- statistic/report ---------------------------------------------------------------------------

func (r *StatisticReads) SubscriptionMonths(ctx context.Context, scope domain.StatisticScope, year domain.StatisticYear, accepted bool) ([]domain.StatisticMonthly, error) {
	status := ``
	if accepted {
		status = ` AND status_flag=4`
	}
	rows := []domain.StatisticMonthly{}
	err := r.db.SelectContext(ctx, &rows, `SELECT date_trunc('month', creation_date AT TIME ZONE 'UTC') AS month, count(*)::text AS value
 FROM application_subscription WHERE sport_association_id=$1 AND `+statisticGroup+` AND NOT archived
 AND creation_date BETWEEN $3 AND $4`+status+` GROUP BY 1`, scope.Association, scope.Group, year.From, year.To)
	return rows, err
}

const statisticActiveCourses = `SELECT course_id FROM application_course WHERE NOT deleted AND ($2::uuid IS NULL OR group_id=$2::uuid)
 AND sport_association_id=$1 AND status_flag=2 AND creation_date BETWEEN $3 AND $4`

func (r *StatisticReads) ActiveCourseCount(ctx context.Context, scope domain.StatisticScope, year domain.StatisticYear) (int64, error) {
	var count int64
	err := r.db.GetContext(ctx, &count, `SELECT count(*) FROM (`+statisticActiveCourses+`) courses`, scope.Association, scope.Group, year.From, year.To)
	return count, err
}

func (r *StatisticReads) CourseGroups(ctx context.Context, scope domain.StatisticScope, year domain.StatisticYear) ([]domain.StatisticCourseGroup, error) {
	rows := []domain.StatisticCourseGroup{}
	err := r.db.SelectContext(ctx, &rows, `SELECT c.title, count(cs.course_subscription_id) AS count, sum(c.fee)::text AS revenue
 FROM application_coursesubscription cs JOIN application_course c ON c.course_id=cs.course_id
 WHERE NOT cs.deleted AND cs.course_id IN (`+statisticActiveCourses+`)
 AND ($5::uuid IS NULL OR (cs.subscription_id IN (SELECT subscription_id FROM application_subscription WHERE sport_association_id=$5::uuid)
 AND c.sport_association_id=$5::uuid)) GROUP BY c.title`, scope.Association, scope.Group, year.From, year.To, scope.Impersonation)
	return rows, err
}

func (r *StatisticReads) StatusCounts(ctx context.Context, scope domain.StatisticScope) ([]domain.StatisticKeyCount, error) {
	rows := []domain.StatisticKeyCount{}
	err := r.db.SelectContext(ctx, &rows, `SELECT status_flag AS key, count(*) AS count FROM application_subscription
 WHERE sport_association_id=$1 AND `+statisticGroup+` AND status_flag <= 4 GROUP BY status_flag ORDER BY status_flag`,
		scope.Association, scope.Group)
	return rows, err
}

const statisticPaymentRange = `((payment_date BETWEEN $3 AND $4 AND payment_date IS NOT NULL)
 OR (creation_date BETWEEN $3 AND $4 AND payment_date IS NULL))`

func (r *StatisticReads) PaymentMonths(ctx context.Context, scope domain.StatisticScope, year domain.StatisticYear) ([]domain.StatisticMonthly, error) {
	rows := []domain.StatisticMonthly{}
	err := r.db.SelectContext(ctx, &rows, `SELECT date_trunc('month', creation_date AT TIME ZONE 'UTC') AS month, sum(amount)::text AS value
 FROM application_payment WHERE NOT deleted AND `+statisticGroup+` AND sport_association_id=$1 AND NOT archived AND NOT expense AND paid
 AND `+statisticPaymentRange+` GROUP BY 1 ORDER BY 1`, scope.Association, scope.Group, year.From, year.To)
	return rows, err
}

func (r *StatisticReads) PaymentTotals(ctx context.Context, scope domain.StatisticScope, year domain.StatisticYear) (domain.StatisticPaymentTotals, error) {
	var totals domain.StatisticPaymentTotals
	err := r.db.GetContext(ctx, &totals, `SELECT (sum(amount) FILTER (WHERE NOT expense AND paid))::text AS paid,
 (sum(amount) FILTER (WHERE NOT expense AND NOT paid))::text AS unpaid,
 (sum(amount) FILTER (WHERE subject=1 AND paid))::text AS subscriptions,
 (sum(amount) FILTER (WHERE subject=2 AND paid))::text AS activities, count(*) AS count
 FROM application_payment WHERE NOT deleted AND `+statisticGroup+` AND sport_association_id=$1 AND NOT archived
 AND `+statisticPaymentRange, scope.Association, scope.Group, year.From, year.To)
	return totals, err
}

func (r *StatisticReads) Accepted(ctx context.Context, scope domain.StatisticScope) ([]domain.StatisticAccepted, error) {
	rows := []domain.StatisticAccepted{}
	err := r.db.SelectContext(ctx, &rows, `SELECT s.associate_id IS NOT NULL AS has_associate, a.born_date, a.sex
 FROM application_subscription s LEFT JOIN application_associate a ON a.associate_id=s.associate_id
 WHERE s.sport_association_id=$1 AND ($2::uuid IS NULL OR s.group_id=$2::uuid) AND NOT s.archived AND s.status_flag=4`,
		scope.Association, scope.Group)
	return rows, err
}

func (r *StatisticReads) InvoiceCount(ctx context.Context, scope domain.StatisticScope, year domain.StatisticYear) (int64, error) {
	var count int64
	err := r.db.GetContext(ctx, &count, `SELECT count(*) FROM application_invoice WHERE NOT deleted AND `+statisticGroup+`
 AND sport_association_id=$1 AND NOT archived AND creation_date BETWEEN $3 AND $4`, scope.Association, scope.Group, year.From, year.To)
	return count, err
}

// --- statistic/athlete-dashboard ----------------------------------------------------------------

func (r *StatisticReads) PrimaryAssociation(ctx context.Context) (*string, bool, error) {
	var id *string
	err := r.db.GetContext(ctx, &id, `SELECT primary_association_id::text FROM instance_instanceconfiguration ORDER BY id LIMIT 1`)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, false, nil
	}
	return id, err == nil, err
}

func (r *StatisticReads) AssociationCard(ctx context.Context, association string) (domain.StatisticAssociationCard, error) {
	var card domain.StatisticAssociationCard
	err := r.db.GetContext(ctx, &card, `SELECT a.sport_association_id, a.denomination, a.review_url, a.review_url_enabled, u.user_id,
 u.avatar_image, u.first_name, u.last_name, u.username FROM application_sportassociation a JOIN bakney_user u ON u.user_id=a.user_id
 WHERE a.sport_association_id=$1`, association)
	return card, err
}

// statisticVisible is scoped_queryset's athlete boundary over Subscription for
// $1 person, $2 association, $3 group (always applied: $4 is the scope flag).
const statisticVisible = `($4::uuid IS NULL OR %s IN (SELECT v.subscription_id FROM application_subscription v
 WHERE v.sport_association_id=$4::uuid AND ($3::uuid IS NULL OR v.group_id=$3::uuid) AND (v.user_id=$1 OR v.associate_id IN (
 SELECT m.associate_id FROM application_associate m WHERE NOT m.deleted AND ($3::uuid IS NULL OR m.group_id=$3::uuid)
 AND m.sport_association_id=$4::uuid AND (m.user_id=$1 OR m.tax_code IN (SELECT t.tax_code FROM application_subscription u
 JOIN application_associate t ON t.associate_id=u.associate_id WHERE u.user_id=$1 AND u.sport_association_id=$4::uuid
 AND ($3::uuid IS NULL OR u.group_id=$3::uuid) AND t.tax_code IS NOT NULL AND t.tax_code<>''))))))`

func statisticVisibleFor(column string) string {
	return strings.Replace(statisticVisible, "%s", column, 1)
}

func (r *StatisticReads) AthleteSubscriptions(ctx context.Context, scope domain.StatisticAthleteScope, user, association string) ([]domain.StatisticAthleteSubscription, error) {
	rows := []domain.StatisticAthleteSubscription{}
	err := r.db.SelectContext(ctx, &rows, `WITH codes AS (SELECT a.tax_code FROM application_subscription s
 JOIN application_associate a ON a.associate_id=s.associate_id WHERE s.user_id=$1 AND s.sport_association_id=$2
 AND ($3::uuid IS NULL OR s.group_id=$3::uuid) AND `+familyTaxCodeFilled("a")+` AND `+statisticVisibleFor("s.subscription_id")+`)
 SELECT DISTINCT s.subscription_id, s.start_date, s.end_date, s.archived, s.associate_id IS NOT NULL AS has_associate,
 a.associate_id, a.first_name, a.last_name FROM application_subscription s LEFT JOIN application_associate a ON a.associate_id=s.associate_id
 WHERE s.sport_association_id=$2 AND ($3::uuid IS NULL OR s.group_id=$3::uuid)
 AND (s.user_id=$1 OR a.user_id=$1 OR a.tax_code IN (SELECT tax_code FROM codes)) AND `+statisticVisibleFor("s.subscription_id"),
		user, association, scope.Group, scope.Association)
	return rows, err
}

func (r *StatisticReads) Communications(ctx context.Context, association string, since time.Time) ([]domain.StatisticCommunication, error) {
	rows := []domain.StatisticCommunication{}
	err := r.db.SelectContext(ctx, &rows, `SELECT m.message, t.sent_on FROM communications_messagetransaction t
 JOIN communications_message m ON m.message_id=t.message_id WHERE t.recipient=$1 AND t.sent_on >= $2 AND m.type='INSIDE_APP'
 ORDER BY t.sent_on DESC LIMIT 5`, association, since)
	return rows, err
}

func (r *StatisticReads) SubscriptionCourses(ctx context.Context, scope domain.StatisticAthleteScope, subscription string) ([]domain.StatisticAthleteCourse, error) {
	rows := []domain.StatisticAthleteCourse{}
	err := r.db.SelectContext(ctx, &rows, `SELECT cs.course_subscription_id, c.course_id, c.title AS course_title
 FROM application_coursesubscription cs JOIN application_course c ON c.course_id=cs.course_id
 WHERE NOT cs.deleted AND cs.subscription_id=$2 AND ($4::uuid IS NULL OR (c.sport_association_id=$4::uuid AND cs.subscription_id IN (
 SELECT subscription_id FROM application_subscription WHERE sport_association_id=$4::uuid))) AND `+statisticVisibleFor("cs.subscription_id"),
		scope.Person, subscription, scope.Group, scope.Association)
	return rows, err
}

func (r *StatisticReads) CourseRegistries(ctx context.Context, scope domain.StatisticAthleteScope, course string) ([]domain.StatisticRegistry, error) {
	rows := []domain.StatisticRegistry{}
	err := r.db.SelectContext(ctx, &rows, `SELECT g.attendance_registry_id, g.events::text AS events FROM application_attendanceregistry g
 JOIN application_course c ON c.course_id=g.course_id WHERE g.course_id=$1 AND ($2::uuid IS NULL OR c.sport_association_id=$2::uuid)`,
		course, scope.Association)
	return rows, err
}

func (r *StatisticReads) FirstAttendanceDay(ctx context.Context, scope domain.StatisticAthleteScope, registry string, day string) (*domain.StatisticAttendanceDay, error) {
	var row domain.StatisticAttendanceDay
	err := r.db.GetContext(ctx, &row, `SELECT d.attendance_day_id, d.attendees::text AS attendees, d.expected_absences::text AS expected_absences
 FROM application_attendanceday d JOIN application_attendanceregistry g ON g.attendance_registry_id=d.attendance_registry_id
 JOIN application_course c ON c.course_id=g.course_id WHERE d.attendance_registry_id=$1 AND (d.date AT TIME ZONE 'UTC')::date=$2::date
 AND NOT d.auto_marked AND ($3::uuid IS NULL OR c.sport_association_id=$3::uuid) ORDER BY d.attendance_day_id LIMIT 1`,
		registry, day, scope.Association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// --- expiredPayments --------------------------------------------------------------------------

func (r *StatisticReads) ExpiredPayments(ctx context.Context, scope domain.StatisticScope, from, to time.Time) ([]domain.StatisticExpiredPayment, error) {
	rows := []domain.StatisticExpiredPayment{}
	err := r.db.SelectContext(ctx, &rows, `SELECT p.payment_id, p.amount::text AS amount, p.payment_date, p.creation_date, p.subject,
 p.meta::text AS meta, p.course::text AS course, p.description, pc.payment_category_id IS NOT NULL AS has_category, pc.name AS category_name,
 a.associate_id, a.first_name, a.last_name
 FROM application_payment p JOIN application_associate a ON a.associate_id=p.associate_id
 LEFT JOIN application_paymentcategory pc ON pc.payment_category_id=p.payment_category_id
 WHERE NOT p.deleted AND ($2::uuid IS NULL OR p.group_id=$2::uuid) AND NOT p.paid AND NOT p.archived AND p.sport_association_id=$1
 AND NOT p.expense AND p.amount > 0 AND p.creation_date BETWEEN $3 AND $4
 AND ($5::uuid IS NULL OR p.sport_association_id=$5::uuid)
 ORDER BY p.creation_date DESC, p.payment_date DESC, p.payment_id LIMIT 100`, scope.Association, scope.Group, from, to, scope.Impersonation)
	return rows, err
}

// ExpiredPaymentSubscriptions keeps the view's join through associate__payment:
// a DateField compared with the payment's timestamp (midnight UTC).
func (r *StatisticReads) ExpiredPaymentSubscriptions(ctx context.Context, scope domain.StatisticScope, associates []string) ([]domain.StatisticSubscriptionSpan, error) {
	rows := []domain.StatisticSubscriptionSpan{}
	err := r.db.SelectContext(ctx, &rows, `SELECT s.subscription_id, s.associate_id, s.start_date, s.end_date
 FROM application_subscription s JOIN application_payment p ON p.associate_id=s.associate_id
 WHERE NOT s.archived AND s.sport_association_id=$1 AND ($2::uuid IS NULL OR s.group_id=$2::uuid)
 AND ($4::uuid IS NULL OR s.sport_association_id=$4::uuid) AND s.associate_id=ANY($3::uuid[])
 AND (s.start_date::timestamp AT TIME ZONE 'UTC') <= p.payment_date AND (s.end_date::timestamp AT TIME ZONE 'UTC') >= p.payment_date
 ORDER BY s.creation_date DESC, s.subscription_id, p.payment_id`, scope.Association, scope.Group, uuidArray(associates), scope.Impersonation)
	return rows, err
}

// PaymentLink is get_course_carnet_name's lookups: the first course
// subscription (all_objects), else the first group-aware installment
// (all_objects), else the first carnet subscription paid by the payment.
func (r *StatisticReads) PaymentLink(ctx context.Context, payment string, group *string) (domain.StatisticPaymentLink, error) {
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
		err := r.db.GetContext(ctx, &row, lookup.query, lookup.args...)
		if errors.Is(err, sql.ErrNoRows) {
			continue
		}
		if err != nil {
			return domain.StatisticPaymentLink{}, err
		}
		return domain.StatisticPaymentLink{Slot: slot, Title: row.Title, MissingCarnet: row.Missing}, nil
	}
	return domain.StatisticPaymentLink{Slot: -1}, nil
}
