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

// CourseAttendance serves attendees, attendees_update, subscription_attendance
// and attendance_mark. Queries keep the views' filters, their first() key
// order and the absence of ORDER BY where Django iterates in database order.
type CourseAttendance struct{ db *sqlx.DB }

func NewCourseAttendance(db *sqlx.DB) *CourseAttendance { return &CourseAttendance{db: db} }

var _ domain.CourseAttendanceStore = (*CourseAttendance)(nil)

type courseAttendanceTransaction struct{ *archiveWriteTransaction }

func (r *CourseAttendance) BeginCourseAttendance(ctx context.Context) (domain.CourseAttendanceTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &courseAttendanceTransaction{archiveWriteTransaction: &archiveWriteTransaction{
		paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}}, nil
}

func (t *courseAttendanceTransaction) OwnerAssociation(ctx context.Context, owner string, scope *string) (string, error) {
	return paymentCategoryAssociation(ctx, t.tx, owner, scope, false)
}

func attendanceNoRows[T any](row *T, err error) (*T, error) {
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return row, nil
}

func (t *courseAttendanceTransaction) Course(ctx context.Context, id, association string, group, scope *string) (*domain.AttendanceCourse, error) {
	var course domain.AttendanceCourse
	err := t.tx.GetContext(ctx, &course, `SELECT course_id, title, sport_association_id FROM application_course
 WHERE course_id=$1::uuid AND NOT deleted AND sport_association_id=$2::uuid AND ($3::uuid IS NULL OR group_id=$3::uuid)
 AND ($4::uuid IS NULL OR sport_association_id=$4::uuid) ORDER BY course_id LIMIT 1`, id, association, group, scope)
	return attendanceNoRows(&course, err)
}

const attendanceRegistryScope = `($2::uuid IS NULL OR r.course_id IN (SELECT course_id FROM application_course WHERE sport_association_id=$2::uuid))`

func (t *courseAttendanceTransaction) FirstRegistry(ctx context.Context, course string, scope *string) (*domain.AttendanceRegistryRow, error) {
	var registry domain.AttendanceRegistryRow
	err := t.tx.GetContext(ctx, &registry, `SELECT r.attendance_registry_id, r.course_id, r.events::text AS events
 FROM application_attendanceregistry r WHERE r.course_id=$1::uuid AND `+attendanceRegistryScope+`
 ORDER BY r.attendance_registry_id LIMIT 1`, course, scope)
	return attendanceNoRows(&registry, err)
}

// attendanceDayColumns select a day (alias d) with its registry's course (base manager).
const attendanceDayColumns = `d.attendance_day_id, d.attendance_registry_id, d.title, d.date, d.attendees::text AS attendees,
 d.expected_absences::text AS expected_absences, d.associated_event::text AS associated_event, c.course_id, c.title AS course_title,
 c.sport_association_id AS course_association_id
 FROM application_attendanceday d JOIN application_attendanceregistry r ON r.attendance_registry_id=d.attendance_registry_id
 JOIN application_course c ON c.course_id=r.course_id`

const attendanceDayScope = `($2::uuid IS NULL OR c.sport_association_id=$2::uuid)`

func (t *courseAttendanceTransaction) FirstUnlinkedDay(ctx context.Context, registry string) (*domain.AttendanceDayRow, error) {
	var day domain.AttendanceDayRow
	err := t.tx.GetContext(ctx, &day, `SELECT `+attendanceDayColumns+`
 WHERE d.attendance_registry_id=$1::uuid AND d.associated_event IS NULL ORDER BY d.attendance_day_id LIMIT 1`, registry)
	return attendanceNoRows(&day, err)
}

func (t *courseAttendanceTransaction) DayWithEventExists(ctx context.Context, registry, event string) (bool, error) {
	var exists bool
	err := t.tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_attendanceday
 WHERE attendance_registry_id=$1::uuid AND associated_event=$2::uuid)`, registry, event)
	return exists, err
}

func (t *courseAttendanceTransaction) RegistryDays(ctx context.Context, registry string, scope *string) ([]domain.AttendanceDayRow, error) {
	days := []domain.AttendanceDayRow{}
	err := t.tx.SelectContext(ctx, &days, `SELECT `+attendanceDayColumns+`
 WHERE d.attendance_registry_id=$1::uuid AND `+attendanceDayScope+` ORDER BY d.date`, registry, scope)
	return days, err
}

func (t *courseAttendanceTransaction) Day(ctx context.Context, id string, registry, owner *string, scope *string) (*domain.AttendanceDayRow, error) {
	var day domain.AttendanceDayRow
	err := t.tx.GetContext(ctx, &day, `SELECT `+attendanceDayColumns+`
 WHERE d.attendance_day_id=$1::uuid AND `+attendanceDayScope+` AND ($3::uuid IS NULL OR d.attendance_registry_id=$3::uuid)
 AND `+ownedAssociation("c.sport_association_id", "4")+`
 ORDER BY d.attendance_day_id LIMIT 1`, id, scope, registry, owner)
	return attendanceNoRows(&day, err)
}

func (t *courseAttendanceTransaction) TodayDay(ctx context.Context, registry, date string, scope *string) (*domain.AttendanceDayRow, error) {
	var day domain.AttendanceDayRow
	err := t.tx.GetContext(ctx, &day, `SELECT `+attendanceDayColumns+`
 WHERE d.attendance_registry_id=$1::uuid AND `+attendanceDayScope+` AND (d.date AT TIME ZONE 'UTC')::date=$3::date
 ORDER BY d.attendance_day_id LIMIT 1`, registry, scope, date)
	return attendanceNoRows(&day, err)
}

func (t *courseAttendanceTransaction) DaysOfRegistries(ctx context.Context, courses []string, scope *string) ([]domain.AttendanceDayRow, error) {
	days := []domain.AttendanceDayRow{}
	err := t.tx.SelectContext(ctx, &days, `SELECT `+attendanceDayColumns+`
 WHERE d.attendance_registry_id IN (SELECT r.attendance_registry_id FROM application_attendanceregistry r
  WHERE r.course_id=ANY($1::uuid[]) AND `+attendanceRegistryScope+`) AND `+attendanceDayScope+` ORDER BY d.date DESC`, uuidArray(courses), scope)
	return days, err
}

// attendanceFamily is scoped_queryset's athlete visibility inside association
// $A for person $P (Subscription.objects has no soft-delete filter;
// Associate.objects excludes deleted rows).
const attendanceFamily = `(SELECT fs.subscription_id FROM application_subscription fs WHERE fs.sport_association_id=$A::uuid
 AND (fs.user_id=$P::uuid OR fs.associate_id IN (SELECT fa.associate_id FROM application_associate fa
  WHERE fa.sport_association_id=$A::uuid AND NOT fa.deleted AND (fa.user_id=$P::uuid OR fa.tax_code IN (
   SELECT ta.tax_code FROM application_subscription ts JOIN application_associate ta ON ta.associate_id=ts.associate_id
   WHERE ts.user_id=$P::uuid AND ts.sport_association_id=$A::uuid AND ta.tax_code IS NOT NULL AND ta.tax_code<>'')))))`

// ownedAssociation is <relation>__sport_association__user=owner at parameter
// $n (a join through the association, deleted or not), or no filter.
func ownedAssociation(column, n string) string {
	return `($` + n + `::uuid IS NULL OR ` + column + ` IN (SELECT sport_association_id FROM application_sportassociation
  WHERE user_id=$` + n + `::uuid))`
}

func attendanceFamilyOf(association, person string) string {
	return strings.ReplaceAll(strings.ReplaceAll(attendanceFamily, "$A", association), "$P", person)
}

const attendanceCourseSubscriptionColumns = `cs.course_subscription_id, cs.course_id, c.title AS course_title, cs.subscription_id
 FROM application_coursesubscription cs INNER JOIN application_course c ON c.course_id=cs.course_id`

// courseSubscriptionScope filters cs/c rows with the scope parameters at $a (association) and $p (athlete).
func courseSubscriptionScope(a, p string) string {
	return `($` + a + `::uuid IS NULL OR (cs.subscription_id IN (SELECT subscription_id FROM application_subscription
  WHERE sport_association_id=$` + a + `::uuid) AND c.sport_association_id=$` + a + `::uuid
  AND ($` + p + `::uuid IS NULL OR cs.subscription_id IN ` + attendanceFamilyOf("$"+a, "$"+p) + `)))`
}

func (t *courseAttendanceTransaction) CourseSubscription(ctx context.Context, id string, all bool, scope domain.AttendanceScope) (*domain.AttendanceCourseSubscription, error) {
	var row domain.AttendanceCourseSubscription
	err := t.tx.GetContext(ctx, &row, `SELECT `+attendanceCourseSubscriptionColumns+`
 WHERE cs.course_subscription_id=$1::uuid AND ($2 OR NOT cs.deleted) AND `+courseSubscriptionScope("3", "4")+`
 ORDER BY cs.course_subscription_id LIMIT 1`, id, all, scope.Association, scope.Athlete)
	return attendanceNoRows(&row, err)
}

func (t *courseAttendanceTransaction) CourseSubscriptionFor(ctx context.Context, course, subscription string, scope domain.AttendanceScope) (*domain.AttendanceCourseSubscription, error) {
	var row domain.AttendanceCourseSubscription
	err := t.tx.GetContext(ctx, &row, `SELECT `+attendanceCourseSubscriptionColumns+`
 WHERE cs.course_id=$1::uuid AND cs.subscription_id=$2::uuid AND NOT cs.deleted AND `+courseSubscriptionScope("3", "4")+`
 ORDER BY cs.course_subscription_id LIMIT 1`, course, subscription, scope.Association, scope.Athlete)
	return attendanceNoRows(&row, err)
}

func (t *courseAttendanceTransaction) SubscriptionCourses(ctx context.Context, subscription, course, owner *string, all bool,
	scope domain.AttendanceScope) ([]domain.AttendanceCourseSubscription, error) {
	rows := []domain.AttendanceCourseSubscription{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT `+attendanceCourseSubscriptionColumns+`
 WHERE ($1::uuid IS NULL AND cs.subscription_id IS NULL OR cs.subscription_id=$1::uuid) AND ($2::uuid IS NULL OR cs.course_id=$2::uuid)
 AND ($3 OR NOT cs.deleted) AND `+courseSubscriptionScope("4", "5")+` AND `+ownedAssociation("c.sport_association_id", "6"),
		subscription, course, all, scope.Association, scope.Athlete, owner)
	return rows, err
}

func (t *courseAttendanceTransaction) CourseMembers(ctx context.Context, course string) ([]domain.AttendanceCourseSubscription, error) {
	rows := []domain.AttendanceCourseSubscription{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT `+attendanceCourseSubscriptionColumns+`
 WHERE cs.course_id=$1::uuid AND NOT cs.deleted`, course)
	return rows, err
}

func (t *courseAttendanceTransaction) Person(ctx context.Context, subscription string) (*domain.AttendancePerson, error) {
	var row struct {
		domain.CourseSubscriptionPerson
		HasAssociate bool       `db:"has_associate"`
		FirstName    *string    `db:"first_name"`
		LastName     *string    `db:"last_name"`
		TaxCode      *string    `db:"tax_code"`
		BornDate     *time.Time `db:"born_date"`
	}
	err := t.tx.GetContext(ctx, &row, `SELECT s.subscription_id, s.start_date, s.end_date, s.creation_date, s.medical_id,
 m.expiration_date, m.competitive_medical_certificate, s.associate_id, a.associate_id IS NOT NULL AS has_associate,
 a.first_name, a.last_name, a.tax_code, a.born_date
 FROM application_subscription s LEFT JOIN application_medicalcertificate m ON m.medical_id=s.medical_id
 LEFT JOIN application_associate a ON a.associate_id=s.associate_id WHERE s.subscription_id=$1::uuid`, subscription)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	person := &domain.AttendancePerson{CourseSubscriptionPerson: row.CourseSubscriptionPerson, HasAssociate: row.HasAssociate,
		FirstName: row.FirstName, LastName: row.LastName, TaxCode: row.TaxCode}
	if row.HasAssociate {
		associate := domain.InconsistencyAssociate{FirstName: row.FirstName, LastName: row.LastName, TaxCode: row.TaxCode}
		associate.BornDate = row.BornDate
		person.Associate = &associate
	}
	return person, nil
}

func (t *courseAttendanceTransaction) Subscription(ctx context.Context, id string, association, owner, user, group *string,
	scope domain.AttendanceScope) (*string, error) {
	var found string
	err := t.tx.GetContext(ctx, &found, `SELECT s.subscription_id FROM application_subscription s
 WHERE s.subscription_id=$1::uuid AND ($2::uuid IS NULL OR s.sport_association_id=$2::uuid) AND ($3::uuid IS NULL OR s.user_id=$3::uuid)
 AND ($4::uuid IS NULL OR s.group_id=$4::uuid) AND ($5::uuid IS NULL OR (s.sport_association_id=$5::uuid
  AND ($6::uuid IS NULL OR s.subscription_id IN `+attendanceFamilyOf("$5", "$6")+`)))
 AND `+ownedAssociation("s.sport_association_id", "7")+`
 ORDER BY s.subscription_id LIMIT 1`, id, association, user, group, scope.Association, scope.Athlete, owner)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &found, err
}

func (t *courseAttendanceTransaction) InstructorName(ctx context.Context, id string, courseAssociation *string) (*domain.AttendancePerson, error) {
	var row struct {
		FirstName *string `db:"first_name"`
		LastName  *string `db:"last_name"`
	}
	err := t.tx.GetContext(ctx, &row, `SELECT first_name, last_name FROM application_instructor WHERE instructor_id=$1::uuid
 AND user_id=(SELECT user_id FROM application_sportassociation WHERE sport_association_id=$2::uuid)
 ORDER BY instructor_id LIMIT 1`, id, courseAssociation)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &domain.AttendancePerson{HasAssociate: true, FirstName: row.FirstName, LastName: row.LastName}, nil
}

// Holdings mirrors CarnetSubscription.objects.filter(Q(course_subscription=cs) |
// Q(course_subscription__isnull=True), subscription=..., disabled=False)
// .order_by('-creation_date') (a LEFT JOIN of the through table), scoped.
func (t *courseAttendanceTransaction) Holdings(ctx context.Context, courseSubscription string, subscription, association *string,
	scope *string) ([]domain.AttendanceHolding, error) {
	rows := []domain.AttendanceHolding{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT h.carnet_subscription_id, h.meta::text AS meta, h.payment_id, p.paid AS payment_paid,
 s.sport_association_id AS subscription_association_id
 FROM application_carnetsubscription h
 LEFT OUTER JOIN application_carnetsubscription_course_subscription l ON l.carnetsubscription_id=h.carnet_subscription_id
 LEFT JOIN application_payment p ON p.payment_id=h.payment_id
 LEFT JOIN application_subscription s ON s.subscription_id=h.subscription_id
 WHERE (l.coursesubscription_id=$1::uuid OR l.coursesubscription_id IS NULL) AND NOT h.disabled
 AND ($2::uuid IS NULL AND h.subscription_id IS NULL OR h.subscription_id=$2::uuid)
 AND ($3::uuid IS NULL OR (s.sport_association_id=$3::uuid AND h.carnet_id_id IN
  (SELECT carnet_id FROM application_carnet WHERE sport_association_id=$3::uuid)))
 AND ($4::uuid IS NULL OR s.sport_association_id=$4::uuid)
 ORDER BY h.creation_date DESC`, courseSubscription, subscription, scope, association)
	return rows, err
}

func (t *courseAttendanceTransaction) StoredDay(ctx context.Context, id string) (*domain.AttendanceDayRow, error) {
	var day domain.AttendanceDayRow
	err := t.tx.GetContext(ctx, &day, `SELECT `+attendanceDayColumns+` WHERE d.attendance_day_id=$1::uuid`, id)
	return attendanceNoRows(&day, err)
}

func (t *courseAttendanceTransaction) StoredHoldingMeta(ctx context.Context, id string) (*string, error) {
	var meta *string
	err := t.tx.GetContext(ctx, &meta, `SELECT meta::text FROM application_carnetsubscription WHERE carnet_subscription_id=$1::uuid`, id)
	return meta, err
}

func (t *courseAttendanceTransaction) UpdateDay(ctx context.Context, id string, attendees, absences, event *string) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_attendanceday SET attendees=$2::jsonb, expected_absences=$3::jsonb,
 associated_event=$4::uuid WHERE attendance_day_id=$1::uuid`, id, attendees, absences, event)
	return err
}

func (t *courseAttendanceTransaction) UpdateHoldingMeta(ctx context.Context, id string, meta *string) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_carnetsubscription SET meta=$2::jsonb WHERE carnet_subscription_id=$1::uuid`, id, meta)
	return err
}

func (t *courseAttendanceTransaction) AppendAudit(ctx context.Context, entry domain.AttendanceAudit) error {
	path := entry.ResolutionPath
	var resolution *string
	if entry.AssociationID != nil {
		resolution = &path
	}
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: entry.Model, Key: entry.Key,
		Representation: modelRepresentation(entry.Model) + " object (" + entry.Key + ")", Action: 1, Changes: entry.Changes,
		Context: entry.Context, AssociationID: entry.AssociationID, ResolutionPath: resolution, At: entry.At})
}

func modelRepresentation(model string) string {
	if model == "carnetsubscription" {
		return "CarnetSubscription"
	}
	return "AttendanceDay"
}
