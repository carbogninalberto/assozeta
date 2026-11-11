package service

import (
	"context"
	"encoding/json"
	"errors"
	"math/big"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// CourseCalendars ports the course calendar views of attendee_views.py:
// calendar, calendar_update (POST and DELETE), full_events_calendar and
// full_events_calendar_update. Parsed JSON values keep Python semantics
// (domain.RequestObject dicts, []any lists, json.Number numbers).
type CourseCalendars struct {
	store domain.CalendarStore
	now   func() time.Time
	newID func() string
}

func NewCourseCalendars(store domain.CalendarStore, now func() time.Time, newID func() string) *CourseCalendars {
	return &CourseCalendars{store: store, now: now, newID: newID}
}

const (
	calendarRegistryTable = "application_attendanceregistry"
	calendarDayTable      = "application_attendanceday"
	calendarReminderTable = "application_reminders"
	calendarGlobalTable   = "application_globalcalendarevents"
	calendarDraft         = 1
	calendarPublished     = 2
)

var calendarColors = []string{"fc-event-primary fc-event-light-primary", "fc-event-secondary fc-event-light-secondary",
	"fc-event-warning fc-event-light-warning", "fc-event-danger fc-event-light-danger", "fc-event-info fc-event-light-info",
	"fc-event-success fc-event-light-success", "fc-event-dark fc-event-light-dark"}

var calendarReminderSeconds = map[string]int64{"minutes": 60, "hours": 3600, "days": 86400}
var calendarReminderText = map[string]string{"minutes": "minuti", "hours": "ore", "days": "giorni"}

var (
	errCalendarRaised    = domain.ErrCalendarRaised
	errCalendarNotPorted = domain.ErrCalendarNotPorted
)

func calendarObject(fields ...any) domain.RequestObject {
	object := domain.RequestObject{}
	for i := 0; i+1 < len(fields); i += 2 {
		object = append(object, domain.RequestField{Name: fields[i].(string), Value: fields[i+1]})
	}
	return object
}

func calendarText(value *string) any {
	if value == nil {
		return nil
	}
	return *value
}

func calendarParse(text *string) (any, error) {
	if text == nil {
		return nil, nil
	}
	return dto.ParseProfileTablesJSON([]byte(*text))
}

// calendarItems is `for item in value` for a parsed value; ok=false is the
// TypeError of a non-iterable. Iterating a dict yields its keys and a str its
// characters, both of which the views then index or call .get on (raising).
func calendarItems(value any) ([]any, bool) {
	switch v := value.(type) {
	case []any:
		return v, true
	case domain.RequestObject:
		keys := make([]any, len(v))
		for i, field := range v {
			keys[i] = field.Name
		}
		return keys, true
	case string:
		chars := []any{}
		for _, r := range v {
			chars = append(chars, string(r))
		}
		return chars, true
	}
	return nil, false
}

// calendarUUID prepares a value for a UUIDField lookup: canonical text, or
// nil for None. Strings follow uuid.UUID, integers (and booleans) read as
// uuid.UUID(int=...); anything else raises ValidationError.
func calendarUUID(value any) (*string, error) {
	switch v := value.(type) {
	case nil:
		return nil, nil
	case string:
		id, err := paymentInfoParseUUID(v)
		if err != nil {
			return nil, errCalendarRaised
		}
		text := id.String()
		return &text, nil
	case bool, json.Number:
		// uuid.UUID(int=value); floats, negatives and values past 128 bits raise.
		if text, err := dto.DjangoUUIDFromInt(v); err == nil {
			return &text, nil
		}
	}
	return nil, errCalendarRaised
}

// calendarDate is DateTimeField.to_python for a lookup or an assignment: nil
// is None, a str parses (else ValidationError), anything else raises.
func calendarDate(value any) (*time.Time, error) {
	switch v := value.(type) {
	case nil:
		return nil, nil
	case string:
		if _, _, result := dto.DjangoParseDateTime(v); result == dto.DjangoDateTimeNotPorted {
			return nil, errCalendarNotPorted
		}
		moment, ok := dto.DjangoDateTimePrep(v)
		if !ok {
			return nil, errCalendarRaised
		}
		return &moment, nil
	}
	return nil, errCalendarRaised
}

// calendarChar is a CharField value as stored: str() of the value, None as NULL.
func calendarChar(value any) *string {
	if value == nil {
		return nil
	}
	text := dto.PythonStr(value)
	return &text
}

func calendarTooLong(value *string, limit int) bool {
	return value != nil && utf8.RuneCountInString(*value) > limit
}

func calendarNaive(value time.Time) string {
	value = value.UTC()
	if value.Nanosecond()/1000 == 0 {
		return value.Format("2006-01-02 15:04:05")
	}
	return value.Format("2006-01-02 15:04:05.000000")
}

func calendarJSONRender(value any) string { return string(dto.PythonJSONDumpsSorted(value)) }

func calendarJSONStore(value any) *string {
	text := string(dto.PythonJSONDumps(value))
	return &text
}

// calendarField is auditlog's rendering of one in-memory field value;
// always marks foreign keys and DateTimeFields, whose None differs from the
// absent side's 'None' and is therefore logged on create.
type calendarField struct {
	name, value string
	always      bool
}

func calendarCreateChanges(fields []calendarField) map[string][2]string {
	changes := map[string][2]string{}
	for _, field := range fields {
		if field.always || field.value != "None" {
			changes[field.name] = [2]string{"None", field.value}
		}
	}
	return changes
}

// calendarDiff compares the stored row (as a loaded instance renders) with
// the in-memory values, as auditlog's pre_save diff does.
func calendarDiff(stored map[string][2]string, fields []calendarField) map[string][2]string {
	changes := map[string][2]string{}
	for _, field := range fields {
		old := "None"
		if pair, ok := stored[field.name]; ok {
			old = pair[1]
		}
		if old != field.value {
			changes[field.name] = [2]string{old, field.value}
		}
	}
	return changes
}

func calendarRef(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

// calendarWrite carries one request's audit attribution and lazily resolved
// actor association (auditlog's index fallback).
type calendarWrite struct {
	s        *CourseCalendars
	tx       domain.CalendarTransaction
	identity domain.ResolvedIdentity
	audit    domain.LoginAuditContext
	actor    *string
	resolved bool
}

func (w *calendarWrite) actorAssociation(ctx context.Context) (*string, error) {
	if !w.resolved {
		association, err := w.tx.ActiveAssociationOf(ctx, w.identity.Actor.ID)
		if err != nil {
			return nil, err
		}
		w.actor, w.resolved = association, true
	}
	return w.actor, nil
}

func (w *calendarWrite) log(ctx context.Context, model, key string, action int, changes map[string][2]string, association *string, path string) error {
	if association == nil {
		fallback, err := w.actorAssociation(ctx)
		if err != nil {
			return err
		}
		association, path = fallback, "actor.sport_association"
	}
	return w.tx.AppendAudit(ctx, domain.CalendarAudit{Model: model, Key: key, Action: action, Changes: changes,
		Context: paymentCategoryAuditContext(w.identity, w.audit), AssociationID: association, ResolutionPath: path,
		At: w.s.now().UTC().Truncate(time.Microsecond)})
}

// deleteRow deletes one row; the deleted object no longer resolves, so the
// index uses the changes' sport_association (active rows only) or the actor's.
func (w *calendarWrite) deleteRow(ctx context.Context, table, key, model, id string) error {
	changes, err := w.tx.Delete(ctx, table, key, id)
	if err != nil {
		return err
	}
	if pair, ok := changes["sport_association"]; ok && pair[0] != "None" {
		exists, err := w.tx.ActiveAssociationExists(ctx, pair[0])
		if err != nil {
			return err
		}
		if exists {
			association := pair[0]
			return w.log(ctx, model, id, 2, changes, &association, "changes_json.sport_association")
		}
	}
	return w.log(ctx, model, id, 2, changes, nil, "")
}

// createRow inserts a row, logs auditlog's create entry, then the entry of
// the views' second save() (the in-memory values against the stored row).
func (w *calendarWrite) createRow(ctx context.Context, table, key, model, id string, row domain.CalendarRow, fields []calendarField,
	association *string, path string, saveAgain bool) error {
	if err := w.tx.Insert(ctx, table, row); err != nil {
		return err
	}
	if err := w.log(ctx, model, id, 0, calendarCreateChanges(fields), association, path); err != nil {
		return err
	}
	if !saveAgain {
		return nil
	}
	return w.saveRow(ctx, table, key, model, id, nil, fields, association, path, nil)
}

// saveRow is Model.save() of a loaded row: the pre_save diff is logged before
// the UPDATE, so a database error (failure) still leaves the entry.
func (w *calendarWrite) saveRow(ctx context.Context, table, key, model, id string, row domain.CalendarRow, fields []calendarField,
	association *string, path string, failure error) error {
	stored, err := w.tx.Stored(ctx, table, key, id)
	if err != nil {
		return err
	}
	if changes := calendarDiff(stored, fields); len(changes) > 0 {
		if err = w.log(ctx, model, id, 1, changes, association, path); err != nil {
			return err
		}
	}
	if failure != nil {
		return failure
	}
	if len(row) == 0 {
		return nil
	}
	return w.tx.Update(ctx, table, key, id, row)
}

func (s *CourseCalendars) begin(ctx context.Context, identity domain.ResolvedIdentity, audit domain.LoginAuditContext) (*calendarWrite, error) {
	tx, err := s.store.BeginCalendar(ctx)
	if err != nil {
		return nil, err
	}
	return &calendarWrite{s: s, tx: tx, identity: identity, audit: audit}, nil
}

// ---------------------------------------------------------------------------
// calendar (GET course/<uid>/calendar)
// ---------------------------------------------------------------------------

// Calendar follows `calendar`: AllowAny, so identity may have no scope user.
func (s *CourseCalendars) Calendar(ctx context.Context, identity domain.ResolvedIdentity, raw, group string) (any, error) {
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return nil, err
	}
	w, err := s.begin(ctx, identity, domain.LoginAuditContext{})
	if err != nil {
		return nil, err
	}
	defer w.tx.Rollback()
	scope := paymentCategoryAssociationScope(identity)
	course, err := w.tx.Course(ctx, id.String(), nil, supplierGroup(group), scope)
	if err != nil {
		return nil, err
	}
	if course == nil {
		return nil, domain.ErrCalendarCourseNotFound
	}
	registry, err := w.tx.FirstRegistry(ctx, &course.ID, scope, false)
	if err != nil {
		return nil, err
	}
	sync := false
	if identity.ScopeUser != nil {
		// The scope user is never a collaborator; the view's collaborator
		// branch (re-resolving identity) is not ported.
		if identity.ScopeUser.Role == domain.RoleCollaborator {
			return nil, errCalendarNotPorted
		}
		if sync, err = w.tx.GoogleSyncEnabled(ctx, identity.ScopeUser.ID); err != nil {
			return nil, err
		}
	}
	if course.AssociationID == nil {
		return nil, errCalendarRaised
	}
	card, err := w.tx.AssociationCard(ctx, *course.AssociationID)
	if err != nil {
		return nil, err
	}
	if card == nil {
		return nil, errCalendarRaised
	}
	var events any = []any{}
	var status any = json.Number("1")
	if registry != nil {
		if events, err = calendarParse(registry.Events); err != nil {
			return nil, err
		}
		if events, err = calendarDescribe(events, course.Title); err != nil {
			return nil, err
		}
		status = json.Number(strconv.Itoa(registry.Status))
	}
	return calendarObject("data", calendarObject("status", status, "events", events,
		"course", calendarObject("title", calendarText(course.Title)),
		"sport_association", calendarObject("denomination", calendarText(card.Denomination), "logo", calendarText(card.Logo)),
		"google_calendar_id", calendarText(course.GoogleCalendarID), "google_sync_enabled", sync)), nil
}

// calendarDescribe adds "<course title> - <event title>" to events whose
// extendedProps lack a description.
func calendarDescribe(events any, courseTitle *string) (any, error) {
	items, ok := calendarItems(events)
	if !ok {
		return nil, errCalendarRaised
	}
	list, isList := events.([]any)
	for i, item := range items {
		event, ok := item.(domain.RequestObject)
		if !ok {
			return nil, errCalendarRaised
		}
		props, present := event.Lookup("extendedProps")
		if !present {
			continue
		}
		ext, ok := props.(domain.RequestObject)
		if !ok {
			return nil, errCalendarRaised
		}
		if _, described := ext.Lookup("description"); described {
			continue
		}
		title, ok := event.Lookup("title")
		if !ok {
			return nil, errCalendarRaised
		}
		ext = dto.PythonSet(ext, "description", calendarRef(courseTitle)+" - "+dto.PythonStr(title))
		event = dto.PythonSet(event, "extendedProps", ext)
		if isList {
			list[i] = event
		}
	}
	return events, nil
}

// ---------------------------------------------------------------------------
// reminders (shared by calendar_update and full_events_calendar_update)
// ---------------------------------------------------------------------------

type calendarReminderScope struct {
	association    string
	reminderFilter *string // reminders of the association
	instructorUser *string // instructors of the scope user
}

// reminderValues computes send_at and the reminder text: parse_datetime(start)
// minus int(amount) units.
func reminderValues(event, ext domain.RequestObject) (time.Time, string, error) {
	start, ok := event.Lookup("start")
	if !ok {
		return time.Time{}, "", errCalendarRaised
	}
	text, ok := start.(string)
	if !ok {
		return time.Time{}, "", errCalendarRaised
	}
	moment, _, result := dto.DjangoParseDateTime(text)
	switch result {
	case dto.DjangoDateTimeNotPorted:
		return time.Time{}, "", errCalendarNotPorted
	case dto.DjangoDateTimeNone, dto.DjangoDateTimeValueError:
		return time.Time{}, "", errCalendarRaised
	}
	amount, err := dto.PythonIndex(ext, "reminder_amount")
	if err != nil {
		return time.Time{}, "", errCalendarRaised
	}
	unit, err := dto.PythonIndex(ext, "reminder_unit")
	if err != nil {
		return time.Time{}, "", errCalendarRaised
	}
	number, intResult := dto.PythonInt(amount)
	name, isText := unit.(string)
	seconds, known := calendarReminderSeconds[name]
	if intResult != dto.PythonIntOK || !isText || !known {
		return time.Time{}, "", errCalendarRaised
	}
	total := new(big.Int).Mul(number, big.NewInt(seconds))
	// timedelta(seconds=...) and the subtraction raise OverflowError out of range.
	if total.CmpAbs(big.NewInt(86399999999999)) > 0 {
		return time.Time{}, "", errCalendarRaised
	}
	seconds = total.Int64()
	sendAt := moment.AddDate(0, 0, -int(seconds/86400)).Add(-time.Duration(seconds%86400) * time.Second)
	if sendAt.Year() < 1 || sendAt.Year() > 9999 {
		return time.Time{}, "", errCalendarRaised
	}
	return sendAt, dto.PythonStr(amount) + " " + calendarReminderText[name], nil
}

// reminderInstructor is the views' try block: any failure leaves None.
func (w *calendarWrite) reminderInstructor(ctx context.Context, ext domain.RequestObject, user *string) (*string, error) {
	contains, err := dto.PythonContains(ext, "instructor")
	if err != nil {
		return nil, errCalendarRaised
	}
	if !contains {
		return nil, nil
	}
	value, err := dto.PythonIndex(ext, "instructor")
	if err != nil {
		return nil, nil
	}
	raw, err := dto.PythonIndex(value, "instructor_id")
	if err != nil {
		return nil, nil
	}
	var id string
	switch v := raw.(type) {
	case string:
		parsed, err := paymentInfoParseUUID(v)
		if err != nil {
			return nil, nil
		}
		id = parsed.String()
	case bool, json.Number:
		parsed, err := dto.DjangoUUIDFromInt(v)
		if err != nil {
			return nil, nil
		}
		id = parsed
	default:
		return nil, nil
	}
	return w.tx.Instructor(ctx, id, user)
}

func reminderOptional(ext domain.RequestObject, key string, fallback any) any {
	if value, ok := ext.Lookup(key); ok {
		return value
	}
	return fallback
}

// reminders runs the views' reminder loop over events.
func (w *calendarWrite) reminders(ctx context.Context, events []any, scope calendarReminderScope) error {
	for _, item := range events {
		event := item.(domain.RequestObject)
		rawID, _ := event.Lookup("event_id")
		eventID, err := calendarUUID(rawID)
		if err != nil {
			return err
		}
		var current *domain.CalendarReminder
		if eventID != nil {
			if current, err = w.tx.ReminderByEvent(ctx, *eventID, scope.reminderFilter); err != nil {
				return err
			}
		}
		props, present := event.Lookup("extendedProps")
		if !present {
			continue
		}
		contains, err := dto.PythonContains(props, "reminder_enabled")
		if err != nil {
			return errCalendarRaised
		}
		if !contains {
			continue
		}
		enabled, err := dto.PythonIndex(props, "reminder_enabled")
		if err != nil {
			return errCalendarRaised
		}
		ext := props.(domain.RequestObject)
		switch {
		case current == nil && dto.PythonTruthy(enabled):
			err = w.createReminder(ctx, event, ext, rawID, eventID, scope)
		case current == nil:
		case !dto.PythonTruthy(enabled):
			err = w.deleteRow(ctx, calendarReminderTable, "reminders_id", "reminders", current.ID)
		default:
			err = w.updateReminder(ctx, event, ext, current, scope)
		}
		if err != nil {
			return err
		}
	}
	return nil
}

func reminderTooLong(values ...*string) bool {
	for _, value := range values {
		if calendarTooLong(value, 2000) {
			return true
		}
	}
	return false
}

func (w *calendarWrite) createReminder(ctx context.Context, event, ext domain.RequestObject, rawID any, eventID *string, scope calendarReminderScope) error {
	sendAt, text, err := reminderValues(event, ext)
	if err != nil {
		return err
	}
	instructor, err := w.reminderInstructor(ctx, ext, scope.instructorUser)
	if err != nil {
		return err
	}
	title, ok := event.Lookup("title")
	if !ok {
		return errCalendarRaised
	}
	description := reminderOptional(ext, "description", "")
	courseTitle := reminderOptional(ext, "course_title", nil)
	storedTitle, storedDescription, storedCourse := calendarChar(title), calendarChar(description), calendarChar(courseTitle)
	// NOT NULL and varchar(2000) fail the INSERT (no row, no entry).
	if storedTitle == nil || reminderTooLong(storedTitle, storedDescription, storedCourse, &text) {
		return errCalendarRaised
	}
	id, user := w.s.newID(), w.identity.ScopeUser.ID
	association := scope.association
	row := domain.CalendarRow{"reminders_id": id, "event_id": eventID, "event_title": *storedTitle, "event_description": storedDescription,
		"event_course_title": storedCourse, "event_reminder_text": text, "send_at": sendAt, "user_id": user,
		"instructor_id": instructor, "completed": false, "sport_association_id": association}
	fields := []calendarField{{name: "reminders_id", value: id}, {name: "event_id", value: dto.PythonStr(rawID)},
		{name: "event_title", value: dto.PythonStr(title)}, {name: "event_description", value: dto.PythonStr(description)},
		{name: "event_course_title", value: dto.PythonStr(courseTitle)}, {name: "event_reminder_text", value: text},
		{name: "send_at", value: calendarNaive(sendAt), always: true}, {name: "user", value: user, always: true},
		{name: "instructor", value: calendarRef(instructor), always: true}, {name: "completed", value: "False"},
		{name: "sport_association", value: association, always: true}}
	return w.createRow(ctx, calendarReminderTable, "reminders_id", "reminders", id, row, fields, &association, "direct", true)
}

func (w *calendarWrite) updateReminder(ctx context.Context, event, ext domain.RequestObject, current *domain.CalendarReminder,
	scope calendarReminderScope) error {
	sendAt, text, err := reminderValues(event, ext)
	if err != nil {
		return err
	}
	instructor, err := w.reminderInstructor(ctx, ext, scope.instructorUser)
	if err != nil {
		return err
	}
	title, ok := event.Lookup("title")
	if !ok {
		return errCalendarRaised
	}
	description := reminderOptional(ext, "description", "")
	courseTitle := reminderOptional(ext, "course_title", nil)
	storedTitle, storedDescription, storedCourse := calendarChar(title), calendarChar(description), calendarChar(courseTitle)
	var failure error
	if storedTitle == nil || reminderTooLong(storedTitle, storedDescription, storedCourse, &text) {
		failure = errCalendarRaised
	}
	row := domain.CalendarRow{"event_title": storedTitle, "event_description": storedDescription, "event_course_title": storedCourse,
		"event_reminder_text": text, "send_at": sendAt, "instructor_id": instructor}
	fields := []calendarField{{name: "event_title", value: dto.PythonStr(title)}, {name: "event_description", value: dto.PythonStr(description)},
		{name: "event_course_title", value: dto.PythonStr(courseTitle)}, {name: "event_reminder_text", value: text},
		{name: "send_at", value: calendarNaive(sendAt)}, {name: "instructor", value: calendarRef(instructor)}}
	return w.saveRow(ctx, calendarReminderTable, "reminders_id", "reminders", current.ID, row, fields, current.AssociationID, "direct", failure)
}

// ---------------------------------------------------------------------------
// calendar_update (POST): not atomic
// ---------------------------------------------------------------------------

var calendarUpdated = calendarObject("message", "Calendar updated.")

// UpdatePost follows calendar_update's POST branch. An exception after a
// write commits the earlier work (autocommit) before answering 500.
func (s *CourseCalendars) UpdatePost(ctx context.Context, identity domain.ResolvedIdentity, raw, group string, body any, bodyErr error,
	audit domain.LoginAuditContext) (any, error) {
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return nil, err
	}
	if bodyErr != nil {
		return nil, bodyErr
	}
	data, ok := body.(domain.RequestObject)
	if !ok {
		return nil, errCalendarRaised
	}
	_, hasEvents := data.Lookup("events")
	status, hasStatus := data.Lookup("status")
	if (!hasEvents || !hasStatus) && len(data) != 2 {
		return nil, &domain.CalendarValidation{Detail: "Missing or wrong events data."}
	}
	if !hasStatus {
		return nil, errCalendarRaised
	}
	if !dto.PythonEqual(status, json.Number("1")) && !dto.PythonEqual(status, json.Number("2")) {
		return nil, &domain.CalendarValidation{Detail: "Status not valid."}
	}
	w, err := s.begin(ctx, identity, audit)
	if err != nil {
		return nil, err
	}
	defer w.tx.Rollback()
	scope := paymentCategoryAssociationScope(identity)
	association, err := w.tx.OwnerAssociation(ctx, identity.ScopeUser.ID, scope, false)
	if errors.Is(err, domain.ErrAssociationNotFound) {
		return nil, errCalendarRaised
	}
	if err != nil {
		return nil, err
	}
	course, err := w.tx.Course(ctx, id.String(), &association, supplierGroup(group), scope)
	if err != nil {
		return nil, err
	}
	if course == nil {
		return nil, domain.ErrCalendarCourseNotFound
	}
	registry, err := w.tx.FirstRegistry(ctx, &course.ID, scope, false)
	if err != nil {
		return nil, err
	}
	rawEvents, ok := data.Lookup("events")
	if !ok {
		return nil, errCalendarRaised
	}
	events, err := calendarUpdateEvents(rawEvents)
	if err != nil {
		return nil, err
	}
	var stored any
	if registry != nil && registry.Status == calendarPublished {
		if stored, err = calendarParse(registry.Events); err != nil {
			return nil, err
		}
		if err = calendarMergeSupported(stored, events); err != nil {
			return nil, err
		}
	}
	result, err := w.updatePost(ctx, course, registry, stored, rawEvents, events, status, association)
	var answer *domain.CalendarAnswer
	if errors.Is(err, errCalendarRaised) || errors.As(err, &answer) {
		// Not atomic: the rows saved before the exception stay.
		if commitErr := w.tx.Commit(); commitErr != nil {
			return nil, commitErr
		}
		return nil, err
	}
	if err != nil {
		return nil, err
	}
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	return result, w.tx.Commit()
}

// calendarUpdateEvents is `[event['event_id'] for event in data['events']]`
// plus the event_id__in lookup's UUID validation, both before any write, and
// the refusal of payload shapes whose handling is not ported.
func calendarUpdateEvents(raw any) ([]any, error) {
	items, ok := calendarItems(raw)
	if !ok {
		return nil, errCalendarRaised
	}
	notPorted := false
	for _, item := range items {
		event, ok := item.(domain.RequestObject)
		if !ok {
			return nil, errCalendarRaised
		}
		id, ok := event.Lookup("event_id")
		if !ok {
			return nil, errCalendarRaised
		}
		if _, err := calendarUUID(id); errors.Is(err, errCalendarRaised) {
			return nil, err
		} else if err != nil {
			notPorted = true
		}
		if props, ok := event.Lookup("extendedProps"); ok {
			switch props.(type) {
			case []any, string:
				notPorted = true
			}
		}
		if start, ok := event.Lookup("start"); ok {
			switch v := start.(type) {
			case nil:
			case string:
				if _, _, result := dto.DjangoParseDateTime(v); result == dto.DjangoDateTimeNotPorted {
					notPorted = true
				}
			default:
				notPorted = true
			}
		}
	}
	if notPorted {
		return nil, errCalendarNotPorted
	}
	return items, nil
}

// calendarMergeSupported refuses a published registry whose stored events
// the merge cannot iterate as Python does, or whose sort is not ported.
func calendarMergeSupported(stored any, events []any) error {
	list, ok := stored.([]any)
	if stored == nil {
		return nil
	}
	if !ok {
		return errCalendarNotPorted
	}
	for _, item := range append(append([]any{}, list...), events...) {
		if event, ok := item.(domain.RequestObject); ok {
			if start, ok := event.Lookup("start"); ok {
				switch start.(type) {
				case []any, domain.RequestObject:
					return errCalendarNotPorted
				}
			}
		}
	}
	return nil
}

func calendarStatusValue(status any) int {
	if dto.PythonEqual(status, json.Number("2")) {
		return calendarPublished
	}
	return calendarDraft
}

func (w *calendarWrite) updatePost(ctx context.Context, course *domain.CalendarCourse, registry *domain.CalendarRegistry, stored any,
	rawEvents any, events []any, status any, association string) (any, error) {
	// Reminders of the association; instructors of request.user.
	user := w.identity.ScopeUser.ID
	if err := w.reminders(ctx, events, calendarReminderScope{association: association, reminderFilter: &association,
		instructorUser: &user}); err != nil {
		return nil, err
	}
	switch {
	case registry == nil:
		id := w.s.newID()
		now := w.s.now().UTC().Truncate(time.Microsecond)
		row := domain.CalendarRow{"attendance_registry_id": id, "course_id": course.ID, "creation_date": now,
			"events": calendarJSONStore(rawEvents), "status": calendarStatusValue(status)}
		fields := []calendarField{{name: "attendance_registry_id", value: id}, {name: "course", value: course.ID, always: true},
			{name: "creation_date", value: calendarNaive(now), always: true}, {name: "events", value: calendarJSONRender(rawEvents)},
			{name: "status", value: dto.PythonStr(status)}}
		if err := w.createRow(ctx, calendarRegistryTable, "attendance_registry_id", "attendanceregistry", id, row, fields,
			course.AssociationID, "course.sport_association", true); err != nil {
			return nil, err
		}
		if calendarStatusValue(status) == calendarPublished {
			for _, item := range events {
				if err := w.createDay(ctx, id, course, item.(domain.RequestObject)); err != nil {
					return nil, err
				}
			}
		}
		return calendarUpdated, nil
	case registry.Status == calendarDraft:
		row := domain.CalendarRow{"events": calendarJSONStore(rawEvents), "status": calendarStatusValue(status)}
		fields := []calendarField{{name: "events", value: calendarJSONRender(rawEvents)}, {name: "status", value: dto.PythonStr(status)}}
		if err := w.saveRow(ctx, calendarRegistryTable, "attendance_registry_id", "attendanceregistry", registry.ID, row, fields,
			course.AssociationID, "course.sport_association", nil); err != nil {
			return nil, err
		}
		if calendarStatusValue(status) == calendarPublished {
			for _, item := range events {
				if err := w.createDay(ctx, registry.ID, course, item.(domain.RequestObject)); err != nil {
					return nil, err
				}
			}
		}
		return calendarUpdated, nil
	case registry.Status == calendarPublished:
		return w.mergePublished(ctx, course, registry, stored, events)
	}
	return nil, &domain.CalendarAnswer{Status: 412, Body: calendarObject("message", "Calendar is already published.")}
}

// createDay is AttendanceDay.objects.create(...).save() for one event.
func (w *calendarWrite) createDay(ctx context.Context, registry string, course *domain.CalendarCourse, event domain.RequestObject) error {
	title, ok := event.Lookup("title")
	if !ok {
		return errCalendarRaised
	}
	start, ok := event.Lookup("start")
	if !ok {
		return errCalendarRaised
	}
	rawID, _ := event.Lookup("event_id")
	date, err := calendarDate(start)
	if err != nil {
		return err
	}
	storedTitle := calendarChar(title)
	// NOT NULL date and varchar(125) title fail the INSERT.
	if date == nil || calendarTooLong(storedTitle, 125) {
		return errCalendarRaised
	}
	associated, err := calendarUUID(rawID)
	if err != nil {
		return err
	}
	id := w.s.newID()
	row := domain.CalendarRow{"attendance_day_id": id, "attendance_registry_id": registry, "title": storedTitle, "date": *date,
		"attendees": (*string)(nil), "expected_absences": (*string)(nil), "auto_marked": false, "associated_event": associated}
	fields := []calendarField{{name: "attendance_day_id", value: id}, {name: "attendance_registry", value: registry, always: true},
		{name: "title", value: dto.PythonStr(title)}, {name: "date", value: calendarNaive(*date), always: true},
		{name: "attendees", value: "null"}, {name: "expected_absences", value: "null"}, {name: "auto_marked", value: "False"},
		{name: "associated_event", value: dto.PythonStr(rawID)}}
	return w.createRow(ctx, calendarDayTable, "attendance_day_id", "attendanceday", id, row, fields, course.AssociationID,
		"attendance_registry.course.sport_association", true)
}

func (w *calendarWrite) mergePublished(ctx context.Context, course *domain.CalendarCourse, registry *domain.CalendarRegistry, stored any,
	events []any) (any, error) {
	list, _ := stored.([]any)
	if stored == nil {
		return nil, errCalendarRaised // enumerate(None) or sorted(None)
	}
	scope := paymentCategoryAssociationScope(w.identity)
	for _, item := range events {
		event := item.(domain.RequestObject)
		eventID, _ := event.Lookup("event_id")
		found := -1
		for index, entry := range list {
			current, ok := entry.(domain.RequestObject)
			if !ok {
				return nil, errCalendarRaised
			}
			currentID, ok := current.Lookup("event_id")
			if !ok {
				return nil, errCalendarRaised
			}
			if dto.PythonEqual(currentID, eventID) {
				found = index
				break
			}
		}
		if found < 0 {
			list = append(list, event)
			if err := w.createDay(ctx, registry.ID, course, event); err != nil {
				return nil, err
			}
			continue
		}
		current := list[found].(domain.RequestObject)
		previousStart, ok := current.Lookup("start")
		if !ok {
			return nil, errCalendarRaised
		}
		lookup, err := calendarDate(previousStart)
		if err != nil {
			return nil, err
		}
		day, err := w.tx.DayByDate(ctx, registry.ID, lookup, scope)
		if err != nil {
			return nil, err
		}
		values := map[string]any{}
		for _, key := range []string{"title", "start", "end", "allDay"} {
			value, ok := event.Lookup(key)
			if !ok {
				return nil, errCalendarRaised
			}
			values[key] = value
		}
		current = dto.PythonSet(current, "title", values["title"])
		current = dto.PythonSet(current, "start", dto.PythonStr(values["start"]))
		current = dto.PythonSet(current, "end", dto.PythonStr(values["end"]))
		current = dto.PythonSet(current, "allDay", values["allDay"])
		if className, ok := event.Lookup("className"); ok {
			current = dto.PythonSet(current, "className", className)
		}
		props, ok := event.Lookup("extendedProps")
		if !ok {
			return nil, errCalendarRaised
		}
		current = dto.PythonSet(current, "extendedProps", props)
		list[found] = current
		if day != nil {
			if err = w.updateDay(ctx, day.ID, course, values["title"], values["start"]); err != nil {
				return nil, err
			}
		}
	}
	sorted, err := calendarSortByStart(list)
	if err != nil {
		return nil, err
	}
	row := domain.CalendarRow{"events": calendarJSONStore(sorted)}
	fields := []calendarField{{name: "events", value: calendarJSONRender(sorted)}}
	if err = w.saveRow(ctx, calendarRegistryTable, "attendance_registry_id", "attendanceregistry", registry.ID, row, fields,
		course.AssociationID, "course.sport_association", nil); err != nil {
		return nil, err
	}
	return calendarUpdated, nil
}

// updateDay assigns title and date (the raw start) and saves the day.
func (w *calendarWrite) updateDay(ctx context.Context, id string, course *domain.CalendarCourse, title, start any) error {
	var failure error
	date, err := calendarDate(start)
	if err != nil {
		// to_python raises in auditlog's pre_save, before any entry.
		return err
	}
	dateValue := "None"
	if date != nil {
		dateValue = calendarNaive(*date)
	}
	storedTitle := calendarChar(title)
	if date == nil || calendarTooLong(storedTitle, 125) {
		failure = errCalendarRaised
	}
	row := domain.CalendarRow{"title": storedTitle}
	if date != nil {
		row["date"] = *date
	}
	fields := []calendarField{{name: "title", value: dto.PythonStr(title)}, {name: "date", value: dateValue}}
	return w.saveRow(ctx, calendarDayTable, "attendance_day_id", "attendanceday", id, row, fields, course.AssociationID,
		"attendance_registry.course.sport_association", failure)
}

// calendarSortByStart is sorted(events, key=lambda k: k['start']).
func calendarSortByStart(list []any) ([]any, error) {
	keys := make([]any, len(list))
	for i, item := range list {
		event, ok := item.(domain.RequestObject)
		if !ok {
			return nil, errCalendarRaised
		}
		start, ok := event.Lookup("start")
		if !ok {
			return nil, errCalendarRaised
		}
		keys[i] = start
	}
	sorted := append([]any{}, list...)
	if len(list) < 2 {
		return sorted, nil
	}
	allText, allNumbers := true, true
	for _, key := range keys {
		switch key.(type) {
		case string:
			allNumbers = false
		case json.Number, bool:
			allText = false
		default:
			return nil, errCalendarRaised
		}
	}
	if !allText && !allNumbers {
		return nil, errCalendarRaised
	}
	order := make([]int, len(list))
	for i := range order {
		order[i] = i
	}
	sort.SliceStable(order, func(a, b int) bool {
		if allText {
			return keys[order[a]].(string) < keys[order[b]].(string)
		}
		left, _ := calendarNumber(keys[order[a]])
		right, _ := calendarNumber(keys[order[b]])
		return left.Cmp(right) < 0
	})
	for i, index := range order {
		sorted[i] = list[index]
	}
	return sorted, nil
}

func calendarNumber(value any) (*big.Rat, bool) {
	switch v := value.(type) {
	case bool:
		if v {
			return big.NewRat(1, 1), true
		}
		return new(big.Rat), true
	case json.Number:
		number, ok := new(big.Rat).SetString(string(v))
		return number, ok
	}
	return nil, false
}

// ---------------------------------------------------------------------------
// calendar_update (DELETE): atomic
// ---------------------------------------------------------------------------

var calendarEventDeleted = calendarObject("message", "Event deleted.")

func (s *CourseCalendars) UpdateDelete(ctx context.Context, identity domain.ResolvedIdentity, raw, group string, body any, bodyErr error,
	audit domain.LoginAuditContext) (any, error) {
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return nil, err
	}
	w, err := s.begin(ctx, identity, audit)
	if err != nil {
		return nil, err
	}
	defer w.tx.Rollback()
	scope := paymentCategoryAssociationScope(identity)
	association, err := w.tx.OwnerAssociation(ctx, identity.ScopeUser.ID, scope, false)
	if errors.Is(err, domain.ErrAssociationNotFound) {
		return nil, errCalendarRaised
	}
	if err != nil {
		return nil, err
	}
	course, err := w.tx.Course(ctx, id.String(), &association, supplierGroup(group), scope)
	if err != nil {
		return nil, err
	}
	var courseID *string
	if course != nil {
		courseID = &course.ID
	}
	registry, err := w.tx.FirstRegistry(ctx, courseID, scope, true)
	if err != nil {
		return nil, err
	}
	if registry == nil || registry.Status != calendarPublished {
		return nil, &domain.CalendarAnswer{Status: 400, Body: calendarObject("msg", "cannot delete a draft.")}
	}
	parsed, err := calendarParse(registry.Events)
	if err != nil {
		return nil, err
	}
	list, ok := parsed.([]any)
	if parsed != nil && !ok {
		return nil, errCalendarNotPorted
	}
	if bodyErr != nil {
		return nil, bodyErr
	}
	data, ok := body.(domain.RequestObject)
	if !ok {
		return nil, errCalendarRaised
	}
	for _, item := range list {
		if _, ok := item.(domain.RequestObject); !ok {
			return nil, errCalendarRaised
		}
	}
	result, err := w.updateDelete(ctx, course, registry, list, data, association)
	if err != nil {
		return nil, err
	}
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	return result, w.tx.Commit()
}

func calendarGet(event domain.RequestObject, key string) any {
	value, _ := event.Lookup(key)
	return value
}

func (w *calendarWrite) updateDelete(ctx context.Context, course *domain.CalendarCourse, registry *domain.CalendarRegistry, list []any,
	data domain.RequestObject, association string) (any, error) {
	scope := paymentCategoryAssociationScope(w.identity)
	requested, _ := data.Lookup("event_id")
	if requested == nil {
		ids := []string{}
		for _, item := range list {
			value := calendarGet(item.(domain.RequestObject), "event_id")
			if !dto.PythonTruthy(value) {
				continue
			}
			id, err := calendarUUID(value)
			if err != nil {
				return nil, err
			}
			ids = append(ids, *id)
		}
		if err := w.deleteReminders(ctx, ids, association); err != nil {
			return nil, err
		}
		days, err := w.tx.RegistryDays(ctx, registry.ID)
		if err != nil {
			return nil, err
		}
		for _, day := range days {
			if err = w.deleteRow(ctx, calendarDayTable, "attendance_day_id", "attendanceday", day); err != nil {
				return nil, err
			}
		}
		if err = w.deleteRow(ctx, calendarRegistryTable, "attendance_registry_id", "attendanceregistry", registry.ID); err != nil {
			return nil, err
		}
		id := w.s.newID()
		now := w.s.now().UTC().Truncate(time.Microsecond)
		row := domain.CalendarRow{"attendance_registry_id": id, "course_id": course.ID, "creation_date": now,
			"events": calendarJSONStore([]any{}), "status": calendarDraft}
		fields := []calendarField{{name: "attendance_registry_id", value: id}, {name: "course", value: course.ID, always: true},
			{name: "creation_date", value: calendarNaive(now), always: true}, {name: "events", value: "[]"}, {name: "status", value: "1"}}
		if err = w.createRow(ctx, calendarRegistryTable, "attendance_registry_id", "attendanceregistry", id, row, fields,
			course.AssociationID, "course.sport_association", false); err != nil {
			return nil, err
		}
		return calendarUpdated, nil
	}
	wanted := dto.PythonStr(requested)
	var current domain.RequestObject
	for _, item := range list {
		event := item.(domain.RequestObject)
		if dto.PythonStr(calendarGet(event, "event_id")) == wanted {
			current = event
			break
		}
	}
	if current == nil {
		return calendarEventDeleted, nil
	}
	selected := []any{current}
	if groupID, _ := data.Lookup("groupId"); groupID != nil {
		group := []any{}
		for _, item := range list {
			event := item.(domain.RequestObject)
			props := domain.RequestObject{}
			if value, ok := event.Lookup("extendedProps"); ok {
				object, isObject := value.(domain.RequestObject)
				if !isObject {
					return nil, errCalendarRaised
				}
				props = object
			}
			if dto.PythonEqual(calendarGet(props, "groupId"), groupID) {
				group = append(group, event)
			}
		}
		before, _ := data.Lookup("before")
		start, ok := current.Lookup("start")
		if !ok {
			return nil, errCalendarRaised
		}
		currentStart, err := calendarParseStart(start)
		if err != nil {
			return nil, err
		}
		if flag, isBool := before.(bool); isBool {
			chosen := []any{}
			for _, item := range group {
				value, ok := item.(domain.RequestObject).Lookup("start")
				if !ok {
					return nil, errCalendarRaised
				}
				eventStart, err := calendarParseStart(value)
				if err != nil {
					return nil, err
				}
				if eventStart == nil || currentStart == nil || eventStart.aware != currentStart.aware {
					return nil, errCalendarRaised // TypeError comparing None or naive with aware
				}
				if flag && !eventStart.moment.After(currentStart.moment) || !flag && !eventStart.moment.Before(currentStart.moment) {
					chosen = append(chosen, item)
				}
			}
			group = chosen
		}
		selected = group
		contained := false
		for _, item := range selected {
			if dto.PythonEqual(item, current) {
				contained = true
				break
			}
		}
		if !contained {
			selected = append(selected, current)
		}
	}
	deleted, deletedText := []string{}, map[string]bool{}
	for _, item := range selected {
		value := calendarGet(item.(domain.RequestObject), "event_id")
		if !dto.PythonTruthy(value) {
			continue
		}
		id, err := calendarUUID(value)
		if err != nil {
			return nil, err
		}
		deleted = append(deleted, *id)
		deletedText[dto.PythonStr(value)] = true
	}
	days, err := w.tx.DaysByEvents(ctx, registry.ID, deleted, scope)
	if err != nil {
		return nil, err
	}
	for _, day := range days {
		if err = w.deleteRow(ctx, calendarDayTable, "attendance_day_id", "attendanceday", day); err != nil {
			return nil, err
		}
	}
	legacy := []domain.CalendarLegacyDay{}
	for _, item := range selected {
		event := item.(domain.RequestObject)
		value, ok := event.Lookup("start")
		if !ok {
			value = ""
		}
		start, err := calendarParseStart(value)
		if err != nil {
			return nil, err
		}
		if start != nil {
			legacy = append(legacy, domain.CalendarLegacyDay{Title: calendarChar(calendarGet(event, "title")), Date: start.moment})
		}
	}
	if len(legacy) > 0 {
		ids, err := w.tx.LegacyDays(ctx, registry.ID, legacy, scope)
		if err != nil {
			return nil, err
		}
		for _, day := range ids {
			if err = w.deleteRow(ctx, calendarDayTable, "attendance_day_id", "attendanceday", day); err != nil {
				return nil, err
			}
		}
	}
	if err = w.deleteReminders(ctx, deleted, association); err != nil {
		return nil, err
	}
	kept := []any{}
	for _, item := range list {
		if !deletedText[dto.PythonStr(calendarGet(item.(domain.RequestObject), "event_id"))] {
			kept = append(kept, item)
		}
	}
	row := domain.CalendarRow{"events": calendarJSONStore(kept)}
	fields := []calendarField{{name: "events", value: calendarJSONRender(kept)}}
	if err = w.saveRow(ctx, calendarRegistryTable, "attendance_registry_id", "attendanceregistry", registry.ID, row, fields,
		course.AssociationID, "course.sport_association", nil); err != nil {
		return nil, err
	}
	return calendarEventDeleted, nil
}

type calendarStart struct {
	moment time.Time
	aware  bool
}

// calendarParseStart is parse_datetime(str(value)): nil for None, a raise
// for ValueError.
func calendarParseStart(value any) (*calendarStart, error) {
	moment, aware, result := dto.DjangoParseDateTime(dto.PythonStr(value))
	switch result {
	case dto.DjangoDateTimeParsed:
		return &calendarStart{moment: moment, aware: aware}, nil
	case dto.DjangoDateTimeNone:
		return nil, nil
	case dto.DjangoDateTimeNotPorted:
		return nil, errCalendarNotPorted
	}
	return nil, errCalendarRaised
}

func (w *calendarWrite) deleteReminders(ctx context.Context, events []string, association string) error {
	if len(events) == 0 {
		return nil
	}
	ids, err := w.tx.RemindersByEvents(ctx, events, association)
	if err != nil {
		return err
	}
	for _, id := range ids {
		if err = w.deleteRow(ctx, calendarReminderTable, "reminders_id", "reminders", id); err != nil {
			return err
		}
	}
	return nil
}

// ---------------------------------------------------------------------------
// full_events_calendar (GET)
// ---------------------------------------------------------------------------

// CalendarEventsQuery holds the query parameters (QueryDict.get: the last value).
type CalendarEventsQuery struct {
	Start, End, GetLesson, EventID *string
}

// canManageEvents is global_calendar.can_manage_events for the acting user.
func canManageEvents(identity domain.ResolvedIdentity, action string) (bool, error) {
	user := identity.Effective
	if user == nil || user.Role != domain.RoleCollaborator || user.CollaboratorRole == domain.CollaboratorFull {
		return true, nil
	}
	permission := "association.events." + action
	if len(user.Permissions) == 0 {
		return false, nil
	}
	value, err := dto.ParseProfileTablesJSON(user.Permissions)
	if err != nil {
		return false, err
	}
	if !dto.PythonTruthy(value) {
		return false, nil
	}
	contains, err := dto.PythonContains(value, permission)
	if err != nil {
		return false, errCalendarRaised
	}
	return contains, nil
}

func (s *CourseCalendars) Events(ctx context.Context, identity domain.ResolvedIdentity, query CalendarEventsQuery, group string,
	audit domain.LoginAuditContext) (any, error) {
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return nil, &domain.CalendarAnswer{Status: 403, Body: calendarObject("error", "not allowed.")}
	}
	getLesson := true
	if query.GetLesson != nil {
		switch strings.ToLower(*query.GetLesson) {
		case "true", "t", "yes", "y", "1":
		default:
			getLesson = false
		}
	}
	var start, end *time.Time
	for _, pair := range []struct {
		raw    *string
		target **time.Time
	}{{query.Start, &start}, {query.End, &end}} {
		if pair.raw == nil {
			continue
		}
		moment, ok := dto.CalendarEventDateTime(*pair.raw)
		if !ok {
			return nil, &domain.CalendarValidation{Detail: "Invalid calendar date."}
		}
		*pair.target = &moment
	}
	w, err := s.begin(ctx, identity, audit)
	if err != nil {
		return nil, err
	}
	defer w.tx.Rollback()
	scope := paymentCategoryAssociationScope(identity)
	association, err := w.tx.OwnerAssociation(ctx, identity.ScopeUser.ID, scope, false)
	if errors.Is(err, domain.ErrAssociationNotFound) {
		return nil, errCalendarRaised
	}
	if err != nil {
		return nil, err
	}
	courses, err := w.tx.CourseIDs(ctx, association, supplierGroup(group), scope)
	if err != nil {
		return nil, err
	}
	var instructor *string
	if acting := identity.Effective; acting != nil && acting.Role == domain.RoleCollaborator {
		if instructor, err = w.tx.InstructorFor(ctx, identity.ScopeUser.ID, acting.ID); err != nil {
			return nil, err
		}
		if instructor != nil {
			if courses, err = w.tx.InstructorRegistryCourses(ctx, *instructor, courses, scope); err != nil {
				return nil, err
			}
		}
	}
	colors := map[string]string{}
	for index, course := range courses {
		color := calendarColors[0]
		if index < len(calendarColors) {
			color = calendarColors[index]
		}
		colors[course] = color
	}
	registries, err := w.tx.Registries(ctx, courses, scope)
	if err != nil {
		return nil, err
	}
	titles := map[string]*string{}
	events := []any{}
	for _, registry := range registries {
		if _, ok := titles[registry.CourseID]; !ok {
			course, err := w.tx.CourseByID(ctx, registry.CourseID)
			if err != nil {
				return nil, err
			}
			titles[registry.CourseID] = course.Title
		}
		listed, err := w.registryEvents(ctx, registry, instructor, query.EventID, start, end, colors, titles[registry.CourseID], getLesson, scope)
		if err != nil {
			return nil, calendarEventsLate(w.tx, err)
		}
		events = append(events, listed...)
	}
	readable, err := canManageEvents(identity, "read")
	if err != nil {
		return nil, err
	}
	if readable {
		global, err := w.globalCalendar(ctx, association)
		if err != nil {
			return nil, err
		}
		stored, err := calendarParse(global.Events)
		if err != nil {
			return nil, err
		}
		if dto.PythonTruthy(stored) {
			items, ok := stored.([]any)
			if !ok {
				return nil, errCalendarRaised
			}
			for _, item := range items {
				event, ok := item.(domain.RequestObject)
				if !ok {
					return nil, errCalendarRaised
				}
				if query.EventID != nil && !dto.PythonEqual(calendarGet(event, "event_id"), *query.EventID) {
					continue
				}
				value, ok := event.Lookup("start")
				if !ok {
					return nil, errCalendarRaised
				}
				moment, ok := dto.CalendarEventDateTime(value)
				if !ok {
					return nil, &domain.CalendarValidation{Detail: "Invalid calendar date."}
				}
				if start != nil && moment.Before(*start) || end != nil && !moment.Before(*end) {
					continue
				}
				id, ok := event.Lookup("event_id")
				if !ok {
					return nil, errCalendarRaised
				}
				events = append(events, dto.PythonSet(event, "id", id))
			}
		}
	}
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	if err = w.tx.Commit(); err != nil {
		return nil, err
	}
	return calendarObject("data", calendarObject("events", events)), nil
}

// calendarEventsLate keeps the work full_events_calendar (not atomic) has
// already saved (a lesson linking its day) when a later event raises; a
// refused input rolls back.
func calendarEventsLate(tx domain.CalendarTransaction, err error) error {
	var validation *domain.CalendarValidation
	if errors.Is(err, errCalendarRaised) || errors.As(err, &validation) {
		if commit := tx.Commit(); commit != nil {
			return commit
		}
	}
	return err
}

func (w *calendarWrite) registryEvents(ctx context.Context, registry domain.CalendarRegistry, instructor, eventID *string, start, end *time.Time,
	colors map[string]string, courseTitle *string, getLesson bool, scope *string) ([]any, error) {
	stored, err := calendarParse(registry.Events)
	if err != nil {
		return nil, err
	}
	if !dto.PythonTruthy(stored) {
		return nil, nil
	}
	items, ok := stored.([]any)
	if !ok {
		return nil, errCalendarRaised
	}
	listed := []any{}
	for _, item := range items {
		event, ok := item.(domain.RequestObject)
		if !ok {
			return nil, errCalendarRaised
		}
		props := domain.RequestObject{}
		value, hasProps := event.Lookup("extendedProps")
		if hasProps {
			if props, ok = value.(domain.RequestObject); !ok {
				return nil, errCalendarRaised
			}
		}
		assigned := calendarGet(props, "instructor")
		if !dto.PythonTruthy(assigned) {
			assigned = []any{}
		}
		if object, ok := assigned.(domain.RequestObject); ok {
			assigned = []any{object}
		}
		if instructor != nil && *instructor != "" {
			matched := false
			switch v := assigned.(type) {
			case []any:
				for _, entry := range v {
					if object, ok := entry.(domain.RequestObject); ok && dto.PythonStr(calendarGet(object, "instructor_id")) == *instructor {
						matched = true
						break
					}
				}
			case string:
			default:
				return nil, errCalendarRaised
			}
			if !matched {
				continue
			}
		}
		if eventID != nil {
			id, ok := event.Lookup("event_id")
			if !ok {
				return nil, errCalendarRaised
			}
			if !dto.PythonEqual(*eventID, id) {
				continue
			}
		}
		if start != nil && end != nil {
			value, ok := event.Lookup("start")
			if !ok {
				return nil, errCalendarRaised
			}
			moment, ok := dto.CalendarEventDateTime(value)
			if !ok {
				return nil, &domain.CalendarValidation{Detail: "Invalid calendar date."}
			}
			if moment.Before(*start) || !moment.Before(*end) {
				continue
			}
		}
		title, ok := event.Lookup("title")
		if !ok {
			return nil, errCalendarRaised
		}
		startValue, ok := event.Lookup("start")
		if !ok {
			return nil, errCalendarRaised
		}
		date, err := calendarDate(startValue)
		if err != nil {
			return nil, err
		}
		var day *string
		if date != nil {
			if day, err = w.tx.DayMatch(ctx, registry.ID, calendarChar(title), date, scope); err != nil {
				return nil, err
			}
		}
		if !hasProps {
			props = domain.RequestObject{}
		}
		if _, ok := event.Lookup("className"); !ok {
			event = dto.PythonSet(event, "className", []any{colors[registry.CourseID]})
		}
		if day != nil {
			props = dto.PythonSet(props, "attendace_day_id", *day)
			if getLesson {
				lesson, err := (&CourseAttendance{now: w.s.now}).CalendarLesson(ctx, w.tx.Attendance(), w.identity, w.audit,
					domain.AttendanceRegistryRow{ID: registry.ID, CourseID: registry.CourseID, Events: registry.Events}, *day, scope)
				if err != nil {
					return nil, err
				}
				props = dto.PythonSet(props, "lesson", lesson)
			}
		}
		props = dto.PythonSet(props, "course", registry.CourseID)
		if _, ok := props.Lookup("description"); !ok {
			props = dto.PythonSet(props, "description", calendarRef(courseTitle)+" - "+dto.PythonStr(title))
		}
		event = dto.PythonSet(event, "extendedProps", props)
		id, ok := event.Lookup("event_id")
		if !ok {
			return nil, errCalendarRaised
		}
		listed = append(listed, dto.PythonSet(event, "id", id))
	}
	return listed, nil
}

// globalCalendar is GlobalCalendarEvents.objects.get_or_create(sport_association=...).
func (w *calendarWrite) globalCalendar(ctx context.Context, association string) (*domain.CalendarGlobal, error) {
	rows, err := w.tx.GlobalCalendars(ctx, association)
	if err != nil {
		return nil, err
	}
	if len(rows) > 1 {
		return nil, errCalendarRaised // MultipleObjectsReturned
	}
	if len(rows) == 1 {
		return &rows[0], nil
	}
	id := w.s.newID()
	row := domain.CalendarRow{"global_calendar_id": id, "sport_association_id": association, "events": (*string)(nil)}
	fields := []calendarField{{name: "global_calendar_id", value: id}, {name: "sport_association", value: association, always: true},
		{name: "events", value: "null"}}
	if err = w.createRow(ctx, calendarGlobalTable, "global_calendar_id", "globalcalendarevents", id, row, fields, &association, "direct", false); err != nil {
		return nil, err
	}
	return &domain.CalendarGlobal{ID: id, AssociationID: &association}, nil
}

// ---------------------------------------------------------------------------
// full_events_calendar_update (POST): atomic
// ---------------------------------------------------------------------------

var calendarEventsAdded = calendarObject("message", "Events added.")

func calendarInvalid(detail string) error { return &domain.CalendarValidation{Detail: detail} }

// calendarKeys is an insertion-ordered dict keyed by Python equality.
type calendarKeys struct {
	keys   []any
	values []any
}

func (d *calendarKeys) index(key any) int {
	for i, existing := range d.keys {
		if dto.PythonEqual(existing, key) {
			return i
		}
	}
	return -1
}

func (d *calendarKeys) set(key, value any) {
	if i := d.index(key); i >= 0 {
		d.values[i] = value
		return
	}
	d.keys, d.values = append(d.keys, key), append(d.values, value)
}

func (d *calendarKeys) pop(key any) {
	if i := d.index(key); i >= 0 {
		d.keys = append(d.keys[:i:i], d.keys[i+1:]...)
		d.values = append(d.values[:i:i], d.values[i+1:]...)
	}
}

func calendarHashable(value any) bool {
	switch value.(type) {
	case domain.RequestObject, []any:
		return false
	}
	return true
}

func (s *CourseCalendars) GlobalUpdate(ctx context.Context, identity domain.ResolvedIdentity, body any, bodyErr error,
	audit domain.LoginAuditContext) (any, error) {
	w, err := s.begin(ctx, identity, audit)
	if err != nil {
		return nil, err
	}
	defer w.tx.Rollback()
	scope := paymentCategoryAssociationScope(identity)
	association, err := w.tx.OwnerAssociation(ctx, identity.ScopeUser.ID, scope, true)
	if errors.Is(err, domain.ErrAssociationNotFound) {
		return nil, errCalendarRaised
	}
	if err != nil {
		return nil, err
	}
	global, err := w.globalCalendar(ctx, association)
	if err != nil {
		return nil, err
	}
	stored, err := calendarParse(global.Events)
	if err != nil {
		return nil, err
	}
	existing := []any{}
	if dto.PythonTruthy(stored) {
		items, ok := stored.([]any)
		if !ok {
			return nil, errCalendarRaised
		}
		existing = items
	}
	previous := &calendarKeys{}
	for _, item := range existing {
		event, ok := item.(domain.RequestObject)
		if !ok {
			return nil, errCalendarRaised
		}
		id, ok := event.Lookup("event_id")
		if !ok || !calendarHashable(id) {
			return nil, errCalendarRaised
		}
		previous.set(id, nil)
	}
	events, err := w.mutateEvents(body, bodyErr, existing)
	if err != nil {
		return nil, err
	}
	current := &calendarKeys{}
	for _, item := range events {
		id, _ := item.(domain.RequestObject).Lookup("event_id")
		current.set(id, nil)
	}
	removed := []string{}
	for _, id := range previous.keys {
		if current.index(id) >= 0 {
			continue
		}
		canonical, err := calendarUUID(id)
		if err != nil {
			return nil, err
		}
		if canonical != nil {
			removed = append(removed, *canonical)
		}
	}
	for _, id := range current.keys {
		if _, err := calendarUUID(id); err != nil {
			return nil, err
		}
	}
	if err = w.deleteReminders(ctx, removed, association); err != nil {
		return nil, err
	}
	user := identity.ScopeUser.ID
	if err = w.reminders(ctx, events, calendarReminderScope{association: association, reminderFilter: &association, instructorUser: &user}); err != nil {
		return nil, err
	}
	row := domain.CalendarRow{"events": calendarJSONStore(events)}
	fields := []calendarField{{name: "events", value: calendarJSONRender(events)}}
	if err = w.saveRow(ctx, calendarGlobalTable, "global_calendar_id", "globalcalendarevents", global.ID, row, fields,
		global.AssociationID, "direct", nil); err != nil {
		return nil, err
	}
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	return calendarEventsAdded, w.tx.Commit()
}

// mutateEvents is global_calendar.mutate_events.
func (w *calendarWrite) mutateEvents(body any, bodyErr error, existing []any) ([]any, error) {
	if bodyErr != nil {
		return nil, bodyErr
	}
	data, ok := body.(domain.RequestObject)
	if !ok {
		return nil, calendarInvalid("Calendar payload must be an object.")
	}
	action, _ := data.Lookup("action")
	rawEvents, hasEvents := data.Lookup("events")
	if !hasEvents {
		rawEvents = []any{}
	}
	incoming, ok := rawEvents.([]any)
	if !ok {
		return nil, calendarInvalid("Events must be a list of objects.")
	}
	for _, item := range incoming {
		if _, ok := item.(domain.RequestObject); !ok {
			return nil, calendarInvalid("Events must be a list of objects.")
		}
	}
	seen := map[string]bool{}
	for _, item := range incoming {
		event := item.(domain.RequestObject)
		id, ok := event.Lookup("event_id")
		text, isText := id.(string)
		if !ok || !isText {
			return nil, calendarInvalid("Invalid event ID.")
		}
		if _, err := paymentInfoParseUUID(text); err != nil {
			return nil, calendarInvalid("Invalid event ID.")
		}
		if seen[text] {
			return nil, calendarInvalid("Duplicate event ID.")
		}
		seen[text] = true
		title, isText := calendarGet(event, "title").(string)
		if !isText || dto.PythonStrip(title) == "" {
			return nil, calendarInvalid("Event title is required.")
		}
		start, ok := dto.CalendarEventDateTime(calendarGet(event, "start"))
		if !ok {
			return nil, calendarInvalid("Invalid calendar date.")
		}
		if end := calendarGet(event, "end"); dto.PythonTruthy(end) {
			moment, ok := dto.CalendarEventDateTime(end)
			if !ok {
				return nil, calendarInvalid("Invalid calendar date.")
			}
			if moment.Before(start) {
				return nil, calendarInvalid("Event end precedes start.")
			}
		}
		props := domain.RequestObject{}
		if value, ok := event.Lookup("extendedProps"); ok {
			object, isObject := value.(domain.RequestObject)
			if !isObject {
				return nil, calendarInvalid("Invalid global event properties.")
			}
			props = object
		}
		if dto.PythonTruthy(calendarGet(props, "course")) {
			return nil, calendarInvalid("Invalid global event properties.")
		}
		if dto.PythonTruthy(calendarGet(props, "reminder_enabled")) {
			raw := calendarGet(props, "reminder_amount")
			amount, result := dto.PythonInt(raw)
			switch result {
			case dto.PythonIntInvalid:
				return nil, calendarInvalid("Invalid reminder amount.")
			case dto.PythonIntOverflow:
				return nil, errCalendarRaised
			}
			unit := calendarGet(props, "reminder_unit")
			if !calendarHashable(unit) {
				return nil, errCalendarRaised // unhashable `in` a dict
			}
			name, isText := unit.(string)
			_, known := calendarReminderText[name]
			_, isBool := raw.(bool)
			if !isText || !known || isBool || amount.Sign() < 0 || amount.Cmp(big.NewInt(525600)) > 0 {
				return nil, calendarInvalid("Invalid reminder settings.")
			}
		}
	}
	old := &calendarKeys{}
	for _, item := range existing {
		id, _ := item.(domain.RequestObject).Lookup("event_id")
		old.set(id, item)
	}
	actionText, isText := action.(string)
	if isText && (actionText == "create" || actionText == "update" || actionText == "delete") {
		allowed, err := canManageEvents(w.identity, actionText)
		if err != nil {
			return nil, err
		}
		if !allowed {
			return nil, &domain.CalendarDenied{Detail: "Missing event permission."}
		}
		if actionText == "delete" {
			id, isText := calendarGet(data, "event_id").(string)
			if !isText || id == "" {
				return nil, calendarInvalid("Event ID is required.")
			}
			old.pop(id)
		} else {
			if len(incoming) != 1 {
				return nil, calendarInvalid("Exactly one event is required.")
			}
			event := incoming[0].(domain.RequestObject)
			id, _ := event.Lookup("event_id")
			exists := old.index(id) >= 0
			if actionText == "create" && exists {
				return nil, calendarInvalid("Event already exists.")
			}
			if actionText == "update" && !exists {
				return nil, &domain.CalendarAnswer{Status: 404, Body: calendarObject("detail", "Event not found.")}
			}
			old.set(id, event)
		}
		return old.values, nil
	}
	if action != nil || !hasEvents {
		return nil, calendarInvalid("Invalid calendar action.")
	}
	incomingKeys := &calendarKeys{}
	for _, item := range incoming {
		id, _ := item.(domain.RequestObject).Lookup("event_id")
		incomingKeys.set(id, item)
	}
	required := []string{}
	for _, id := range incomingKeys.keys {
		if old.index(id) < 0 {
			required = append(required, "create")
			break
		}
	}
	for _, id := range old.keys {
		if incomingKeys.index(id) < 0 {
			required = append(required, "delete")
			break
		}
	}
	for i, id := range incomingKeys.keys {
		if j := old.index(id); j >= 0 && !dto.PythonEqual(incomingKeys.values[i], old.values[j]) {
			required = append(required, "update")
			break
		}
	}
	if len(required) == 0 {
		required = []string{"update"}
	}
	for _, permission := range required {
		allowed, err := canManageEvents(w.identity, permission)
		if err != nil {
			return nil, err
		}
		if !allowed {
			return nil, &domain.CalendarDenied{Detail: "Missing event permission."}
		}
	}
	return incoming, nil
}
