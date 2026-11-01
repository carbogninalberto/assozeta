package repository

import (
	"context"
	"database/sql"
	"errors"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// CourseCalendars serves the course calendar and full events calendar views.
// Queries keep the views' filters and the absence of ORDER BY where Django
// iterates a queryset in database order.
type CourseCalendars struct{ db *sqlx.DB }

func NewCourseCalendars(db *sqlx.DB) *CourseCalendars { return &CourseCalendars{db: db} }

var _ domain.CalendarStore = (*CourseCalendars)(nil)

type courseCalendarTransaction struct{ *archiveWriteTransaction }

func (r *CourseCalendars) BeginCalendar(ctx context.Context) (domain.CalendarTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &courseCalendarTransaction{archiveWriteTransaction: &archiveWriteTransaction{
		paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}}, nil
}

// calendarTables are the only tables the generic row writers accept.
var calendarTables = map[string]bool{"application_attendanceregistry": true, "application_attendanceday": true,
	"application_reminders": true, "application_globalcalendarevents": true}

func (t *courseCalendarTransaction) OwnerAssociation(ctx context.Context, owner string, scope *string, lock bool) (string, error) {
	return paymentCategoryAssociation(ctx, t.tx, owner, scope, lock)
}

// ActiveAssociationExists is SportAssociation.objects.filter(pk=...).first() (not deleted).
func (t *courseCalendarTransaction) ActiveAssociationExists(ctx context.Context, id string) (bool, error) {
	var exists bool
	err := t.tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_sportassociation
 WHERE sport_association_id=$1::uuid AND NOT deleted)`, id)
	return exists, err
}

// Course is the live, group-aware Course.objects row, within the impersonation
// boundary and, when association is given, of that association.
func (t *courseCalendarTransaction) Course(ctx context.Context, id string, association, group, scope *string) (*domain.CalendarCourse, error) {
	var course domain.CalendarCourse
	err := t.tx.GetContext(ctx, &course, `SELECT course_id, title, sport_association_id, google_calendar_id FROM application_course
 WHERE course_id=$1::uuid AND NOT deleted AND ($2::uuid IS NULL OR sport_association_id=$2::uuid)
 AND ($3::uuid IS NULL OR group_id=$3::uuid) AND ($4::uuid IS NULL OR sport_association_id=$4::uuid)
 ORDER BY course_id LIMIT 1`, id, association, group, scope)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &course, err
}

// CourseByID follows a foreign key (the base manager).
func (t *courseCalendarTransaction) CourseByID(ctx context.Context, id string) (*domain.CalendarCourse, error) {
	var course domain.CalendarCourse
	err := t.tx.GetContext(ctx, &course, `SELECT course_id, title, sport_association_id, google_calendar_id FROM application_course
 WHERE course_id=$1::uuid`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &course, err
}

func (t *courseCalendarTransaction) AssociationCard(ctx context.Context, id string) (*domain.CalendarAssociationCard, error) {
	var card domain.CalendarAssociationCard
	err := t.tx.GetContext(ctx, &card, `SELECT denomination, logo FROM application_sportassociation WHERE sport_association_id=$1::uuid`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &card, err
}

// GoogleSyncEnabled is user.integration_google_credentials is not None
// (SQL NULL and JSON null both load as None).
func (t *courseCalendarTransaction) GoogleSyncEnabled(ctx context.Context, user string) (bool, error) {
	var enabled bool
	err := t.tx.GetContext(ctx, &enabled, `SELECT COALESCE(integration_google_credentials <> 'null'::jsonb, false)
 FROM bakney_user WHERE user_id=$1::uuid`, user)
	return enabled, err
}

// FirstRegistry is scoped AttendanceRegistry.objects.filter(course=...).first();
// a None course matches nothing (the foreign key is not null).
func (t *courseCalendarTransaction) FirstRegistry(ctx context.Context, course *string, scope *string, lock bool) (*domain.CalendarRegistry, error) {
	if course == nil {
		return nil, nil
	}
	query := `SELECT r.attendance_registry_id, r.course_id, r.status, r.events::text AS events FROM application_attendanceregistry r
 WHERE r.course_id=$1::uuid AND ($2::uuid IS NULL OR r.course_id IN (SELECT course_id FROM application_course WHERE sport_association_id=$2::uuid))
 ORDER BY r.attendance_registry_id LIMIT 1`
	if lock {
		query += ` FOR UPDATE OF r`
	}
	var registry domain.CalendarRegistry
	err := t.tx.GetContext(ctx, &registry, query, *course, scope)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &registry, err
}

// CourseIDs is scoped Course.objects.filter(sport_association=...).values_list('course_id').
func (t *courseCalendarTransaction) CourseIDs(ctx context.Context, association string, group, scope *string) ([]string, error) {
	ids := []string{}
	err := t.tx.SelectContext(ctx, &ids, `SELECT course_id FROM application_course WHERE NOT deleted AND sport_association_id=$1::uuid
 AND ($2::uuid IS NULL OR group_id=$2::uuid) AND ($3::uuid IS NULL OR sport_association_id=$3::uuid)`, association, group, scope)
	return ids, err
}

func (t *courseCalendarTransaction) InstructorFor(ctx context.Context, user, associated string) (*string, error) {
	var id string
	err := t.tx.GetContext(ctx, &id, `SELECT instructor_id FROM application_instructor WHERE user_id=$1::uuid AND associated_user_id=$2::uuid
 ORDER BY instructor_id LIMIT 1`, user, associated)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &id, err
}

// InstructorRegistryCourses is scoped AttendanceRegistry.objects.filter(events__iregex=instructor)
// .filter(course_id__in=courses).values_list('course_id'): one entry per registry.
func (t *courseCalendarTransaction) InstructorRegistryCourses(ctx context.Context, instructor string, courses []string, scope *string) ([]string, error) {
	ids := []string{}
	err := t.tx.SelectContext(ctx, &ids, `SELECT course_id FROM application_attendanceregistry WHERE events::text ~* $1
 AND course_id = ANY($2::uuid[]) AND ($3::uuid IS NULL OR course_id IN (SELECT course_id FROM application_course WHERE sport_association_id=$3::uuid))`,
		instructor, uuidArray(courses), scope)
	return ids, err
}

func (t *courseCalendarTransaction) Registries(ctx context.Context, courses []string, scope *string) ([]domain.CalendarRegistry, error) {
	rows := []domain.CalendarRegistry{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT attendance_registry_id, course_id, status, events::text AS events FROM application_attendanceregistry
 WHERE course_id = ANY($1::uuid[]) AND ($2::uuid IS NULL OR course_id IN (SELECT course_id FROM application_course WHERE sport_association_id=$2::uuid))`,
		uuidArray(courses), scope)
	return rows, err
}

const calendarDayScope = ` AND ($%d::uuid IS NULL OR attendance_registry_id IN (SELECT r.attendance_registry_id FROM application_attendanceregistry r
 JOIN application_course c ON c.course_id=r.course_id WHERE c.sport_association_id=$%d::uuid))`

func dayScope(n int) string { return strings.ReplaceAll(calendarDayScope, "%d", strconv.Itoa(n)) }

// DayMatch is .filter(attendance_registry=..., title=..., date=...).first(); a None
// title or date is an IS NULL lookup.
func (t *courseCalendarTransaction) DayMatch(ctx context.Context, registry string, title *string, date *time.Time, scope *string) (*string, error) {
	var id string
	err := t.tx.GetContext(ctx, &id, `SELECT attendance_day_id FROM application_attendanceday WHERE attendance_registry_id=$1::uuid
 AND (title=$2 OR ($2::text IS NULL AND title IS NULL)) AND (date=$3 OR ($3::timestamptz IS NULL AND date IS NULL))`+dayScope(4)+`
 ORDER BY attendance_day_id LIMIT 1`, registry, title, date, scope)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &id, err
}

func (t *courseCalendarTransaction) DayByDate(ctx context.Context, registry string, date *time.Time, scope *string) (*domain.CalendarDay, error) {
	var day domain.CalendarDay
	err := t.tx.GetContext(ctx, &day, `SELECT attendance_day_id, title, date FROM application_attendanceday WHERE attendance_registry_id=$1::uuid
 AND (date=$2 OR ($2::timestamptz IS NULL AND date IS NULL))`+dayScope(3)+` ORDER BY attendance_day_id LIMIT 1`, registry, date, scope)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &day, err
}

func (t *courseCalendarTransaction) DaysByEvents(ctx context.Context, registry string, events []string, scope *string) ([]string, error) {
	ids := []string{}
	err := t.tx.SelectContext(ctx, &ids, `SELECT attendance_day_id FROM application_attendanceday WHERE attendance_registry_id=$1::uuid
 AND associated_event = ANY($2::uuid[])`+dayScope(3), registry, uuidArray(events), scope)
	return ids, err
}

func (t *courseCalendarTransaction) LegacyDays(ctx context.Context, registry string, rows []domain.CalendarLegacyDay, scope *string) ([]string, error) {
	seen, ids := map[string]bool{}, []string{}
	for _, row := range rows {
		found := []string{}
		if err := t.tx.SelectContext(ctx, &found, `SELECT attendance_day_id FROM application_attendanceday WHERE attendance_registry_id=$1::uuid
 AND associated_event IS NULL AND (title=$2 OR ($2::text IS NULL AND title IS NULL)) AND date=$3`+dayScope(4), registry, row.Title, row.Date, scope); err != nil {
			return nil, err
		}
		for _, id := range found {
			if !seen[id] {
				seen[id] = true
				ids = append(ids, id)
			}
		}
	}
	sort.Strings(ids)
	return ids, nil
}

func (t *courseCalendarTransaction) RegistryDays(ctx context.Context, registry string) ([]string, error) {
	ids := []string{}
	err := t.tx.SelectContext(ctx, &ids, `SELECT attendance_day_id FROM application_attendanceday WHERE attendance_registry_id=$1::uuid`, registry)
	return ids, err
}

// ReminderByEvent is the views' reminders.filter(event_id=...).first(), with
// the global calendar's association filter when given.
func (t *courseCalendarTransaction) ReminderByEvent(ctx context.Context, event string, association *string) (*domain.CalendarReminder, error) {
	var reminder domain.CalendarReminder
	err := t.tx.GetContext(ctx, &reminder, `SELECT reminders_id, sport_association_id FROM application_reminders WHERE event_id=$1::uuid
 AND ($2::uuid IS NULL OR sport_association_id=$2::uuid) ORDER BY reminders_id LIMIT 1`, event, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &reminder, err
}

func (t *courseCalendarTransaction) RemindersByEvents(ctx context.Context, events []string, association string) ([]string, error) {
	ids := []string{}
	err := t.tx.SelectContext(ctx, &ids, `SELECT reminders_id FROM application_reminders WHERE event_id = ANY($1::uuid[])
 AND sport_association_id=$2::uuid FOR UPDATE`, uuidArray(events), association)
	return ids, err
}

// Instructor is Instructor.objects.filter(instructor_id=..., [user=...]).first().
func (t *courseCalendarTransaction) Instructor(ctx context.Context, id string, user *string) (*string, error) {
	var found string
	err := t.tx.GetContext(ctx, &found, `SELECT instructor_id FROM application_instructor WHERE instructor_id=$1::uuid
 AND ($2::uuid IS NULL OR user_id=$2::uuid) ORDER BY instructor_id LIMIT 1`, id, user)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &found, err
}

func (t *courseCalendarTransaction) GlobalCalendars(ctx context.Context, association string) ([]domain.CalendarGlobal, error) {
	rows := []domain.CalendarGlobal{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT global_calendar_id, sport_association_id, events::text AS events FROM application_globalcalendarevents
 WHERE sport_association_id=$1::uuid ORDER BY global_calendar_id LIMIT 21`, association)
	return rows, err
}

func calendarColumns(row domain.CalendarRow) ([]string, []any) {
	names := make([]string, 0, len(row))
	for name := range row {
		names = append(names, name)
	}
	sort.Strings(names)
	values := make([]any, len(names))
	for i, name := range names {
		values[i] = row[name]
	}
	return names, values
}

func (t *courseCalendarTransaction) Insert(ctx context.Context, table string, row domain.CalendarRow) error {
	if !calendarTables[table] {
		return errors.New("calendar table not allowed")
	}
	names, values := calendarColumns(row)
	marks := make([]string, len(names))
	for i, name := range names {
		marks[i] = "$" + strconv.Itoa(i+1) + calendarCast(name)
	}
	_, err := t.tx.ExecContext(ctx, `INSERT INTO `+table+` (`+strings.Join(names, ",")+`) VALUES (`+strings.Join(marks, ",")+`)`, values...)
	return err
}

func (t *courseCalendarTransaction) Update(ctx context.Context, table, key, id string, row domain.CalendarRow) error {
	if !calendarTables[table] {
		return errors.New("calendar table not allowed")
	}
	names, values := calendarColumns(row)
	sets := make([]string, len(names))
	for i, name := range names {
		sets[i] = name + "=$" + strconv.Itoa(i+2) + calendarCast(name)
	}
	_, err := t.tx.ExecContext(ctx, `UPDATE `+table+` SET `+strings.Join(sets, ",")+` WHERE `+key+`=$1::uuid`, append([]any{id}, values...)...)
	return err
}

func (t *courseCalendarTransaction) Delete(ctx context.Context, table, key, id string) (map[string][2]string, error) {
	if !calendarTables[table] {
		return nil, errors.New("calendar table not allowed")
	}
	changes, _, err := deleteModelWithAudit(ctx, t.tx, table, key, id)
	return changes, err
}

// Stored renders the stored row the way auditlog renders a loaded instance
// (the second element of each create pair).
func (t *courseCalendarTransaction) Stored(ctx context.Context, table, key, id string) (map[string][2]string, error) {
	if !calendarTables[table] {
		return nil, errors.New("calendar table not allowed")
	}
	return createdModelChanges(ctx, t.tx, table, key, id)
}

func (t *courseCalendarTransaction) AppendAudit(ctx context.Context, entry domain.CalendarAudit) error {
	event := modelAuditEntry{Model: entry.Model, Key: entry.Key, Representation: calendarClass[entry.Model] + " object (" + entry.Key + ")",
		Action: entry.Action, Changes: entry.Changes, Context: entry.Context, At: entry.At}
	if entry.AssociationID != nil {
		path := entry.ResolutionPath
		event.AssociationID, event.ResolutionPath = entry.AssociationID, &path
	}
	return appendModelAudit(ctx, t.tx, event)
}

var calendarClass = map[string]string{"attendanceregistry": "AttendanceRegistry", "attendanceday": "AttendanceDay",
	"reminders": "Reminders", "globalcalendarevents": "GlobalCalendarEvents"}

// calendarCast types the JSON columns, which receive Python json.dumps text.
func calendarCast(column string) string {
	switch column {
	case "events", "attendees", "expected_absences":
		return "::jsonb"
	}
	return ""
}

func uuidArray(values []string) string {
	return "{" + strings.Join(values, ",") + "}"
}
