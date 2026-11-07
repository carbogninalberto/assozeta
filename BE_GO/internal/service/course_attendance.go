package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// CourseAttendance ports attendees, attendees_update and
// subscription_attendance (attendee_views.py) and attendance_mark
// (statistic_views.py), with map_unlinked_attendance and
// get_extended_prop_for_attendance_day (utils/attendance_utils.py).
//
// None of these views is atomic: an exception the view does not handle
// (domain.ErrCalendarRaised) commits the work saved before it and answers the
// 500 page; an input whose Python semantics are not ported
// (domain.ErrCalendarNotPorted) rolls everything back and answers the 500 page.
type CourseAttendance struct {
	store domain.CourseAttendanceStore
	now   func() time.Time
}

func NewCourseAttendance(store domain.CourseAttendanceStore, now func() time.Time) *CourseAttendance {
	return &CourseAttendance{store: store, now: now}
}

var attendanceRome = func() *time.Location {
	location, err := time.LoadLocation("Europe/Rome")
	if err != nil {
		panic(err)
	}
	return location
}()

const attendanceKey = "course_subscription_id"

type attendanceRun struct {
	s        *CourseAttendance
	tx       domain.CourseAttendanceTransaction
	identity domain.ResolvedIdentity
	audit    domain.LoginAuditContext
	scope    domain.AttendanceScope
	actor    *string
	resolved bool
}

// run executes one view: the work is committed unless the input is refused
// as not ported (or the database failed).
func (s *CourseAttendance) run(ctx context.Context, identity domain.ResolvedIdentity, audit domain.LoginAuditContext,
	view func(*attendanceRun) (any, error)) (any, error) {
	tx, err := s.store.BeginCourseAttendance(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	scope := domain.AttendanceScope{Association: paymentCategoryAssociationScope(identity)}
	if scope.Association != nil && identity.Effective != nil && identity.Effective.Role == domain.RoleAthlete {
		scope.Athlete = &identity.Effective.ID
	}
	r := &attendanceRun{s: s, tx: tx, identity: identity, audit: audit, scope: scope}
	body, err := view(r)
	var answer *domain.CalendarAnswer
	committed := err == nil || errors.Is(err, domain.ErrCalendarRaised) || errors.As(err, &answer)
	if !committed {
		return nil, err
	}
	if cerr := ctx.Err(); cerr != nil {
		return nil, cerr
	}
	if cerr := tx.Commit(); cerr != nil {
		return nil, cerr
	}
	return body, err
}

func attendanceAnswer(status int, fields ...any) error {
	return &domain.CalendarAnswer{Status: status, Body: calendarObject(fields...)}
}

// ---------------------------------------------------------------------------
// Python value semantics
// ---------------------------------------------------------------------------

// attendanceIndex is value[key] for a str key: only dicts support it.
func attendanceIndex(value any, key string) (any, error) {
	object, ok := value.(domain.RequestObject)
	if !ok {
		return nil, errCalendarRaised
	}
	item, present := object.Lookup(key)
	if !present {
		return nil, errCalendarRaised
	}
	return item, nil
}

func attendanceHas(value any, key string) bool {
	object, ok := value.(domain.RequestObject)
	if !ok {
		return false
	}
	_, present := object.Lookup(key)
	return present
}

func attendanceGet(object domain.RequestObject, key string, fallback any) any {
	if value, present := object.Lookup(key); present {
		return value
	}
	return fallback
}

func attendanceTruthy(value any) bool {
	if object, ok := value.(domain.RequestObject); ok {
		return len(object) > 0
	}
	return dto.PythonTruthy(value)
}

func attendanceHashable(value any) bool {
	switch value.(type) {
	case domain.RequestObject, []any:
		return false
	}
	return true
}

// attendanceIn is `item in container` for parsed JSON values.
func attendanceIn(container, item any) (bool, error) {
	switch c := container.(type) {
	case []any:
		for _, candidate := range c {
			if dto.PythonEqual(candidate, item) {
				return true, nil
			}
		}
		return false, nil
	case domain.RequestObject:
		if !attendanceHashable(item) {
			return false, errCalendarRaised
		}
		key, ok := item.(string)
		if !ok {
			return false, nil
		}
		_, present := c.Lookup(key)
		return present, nil
	case string:
		text, ok := item.(string)
		if !ok {
			return false, errCalendarRaised
		}
		return strings.Contains(c, text), nil
	}
	return false, errCalendarRaised
}

// attendanceNumber is a JSON number or bool as an exact value; ok=false for
// anything else (whose comparison with an int raises TypeError).
func attendanceNumber(value any) (*big.Rat, bool) {
	switch v := value.(type) {
	case bool:
		if v {
			return big.NewRat(1, 1), true
		}
		return new(big.Rat), true
	case json.Number:
		return calendarNumber(v)
	}
	return nil, false
}

// attendanceInteger is an int JSON number (floats and bools are not ported).
func attendanceInteger(value any) (*big.Int, error) {
	number, ok := attendanceNumber(value)
	if !ok {
		return nil, errCalendarRaised
	}
	text := dto.PythonStr(value)
	if _, isBool := value.(bool); isBool || strings.ContainsAny(text, ".eE") || !number.IsInt() {
		return nil, errCalendarNotPorted
	}
	return number.Num(), nil
}

// attendanceEventStart is datetime.strptime(e['start'], '%Y-%m-%dT%H:%M:%S.%fZ').
func attendanceEventStart(event any) (time.Time, error) {
	value, err := attendanceIndex(event, "start")
	if err != nil {
		return time.Time{}, err
	}
	text, ok := value.(string)
	if !ok {
		return time.Time{}, errCalendarRaised
	}
	if dto.StrptimeUnicodeDigits(text) {
		return time.Time{}, errCalendarNotPorted
	}
	start, ok := dto.StrptimeEventStart(text)
	if !ok {
		return time.Time{}, errCalendarRaised
	}
	return start, nil
}

// attendanceSameHour compares strftime('%Y/%m/%d %H:%M')[:-3] of both values.
func attendanceSameHour(start, date time.Time) bool {
	date = date.UTC()
	return start.Year() == date.Year() && start.Month() == date.Month() && start.Day() == date.Day() && start.Hour() == date.Hour()
}

// attendanceStrDate is str() of a stored aware datetime (UTC).
func attendanceStrDate(value time.Time) string { return calendarNaive(value) + "+00:00" }

func attendanceFullName(first, last *string) string {
	text := func(value *string) string {
		if value == nil {
			return "None"
		}
		return *value
	}
	return dto.PythonStrip(text(first) + " " + text(last))
}

func attendanceText(value *string) any {
	if value == nil {
		return nil
	}
	return *value
}

// ---------------------------------------------------------------------------
// shared lookups and saves
// ---------------------------------------------------------------------------

func (r *attendanceRun) now() time.Time { return r.s.now().UTC().Truncate(time.Microsecond) }

// person is subscription.associate (500 when either is None).
func (r *attendanceRun) person(ctx context.Context, subscription *string) (*domain.AttendancePerson, error) {
	if subscription == nil {
		return nil, errCalendarRaised
	}
	person, err := r.tx.Person(ctx, *subscription)
	if err != nil {
		return nil, err
	}
	if person == nil || !person.HasAssociate {
		return nil, errCalendarRaised
	}
	return person, nil
}

func (r *attendanceRun) name(ctx context.Context, subscription *string) (string, error) {
	person, err := r.person(ctx, subscription)
	if err != nil {
		return "", err
	}
	return attendanceFullName(person.FirstName, person.LastName), nil
}

func (r *attendanceRun) log(ctx context.Context, model, key string, changes map[string][2]string, association *string, path string) error {
	if len(changes) == 0 {
		return nil
	}
	if association == nil {
		if !r.resolved {
			actor, err := r.tx.ActiveAssociationOf(ctx, r.identity.Actor.ID)
			if err != nil {
				return err
			}
			r.actor, r.resolved = actor, true
		}
		association, path = r.actor, "actor.sport_association"
	}
	return r.tx.AppendAudit(ctx, domain.AttendanceAudit{Model: model, Key: key, Changes: changes,
		Context: paymentCategoryAuditContext(r.identity, r.audit), AssociationID: association, ResolutionPath: path, At: r.now()})
}

// saveDay is AttendanceDay.save() of a loaded day: auditlog's diff of the
// stored row against the in-memory values, then every column.
func (r *attendanceRun) saveDay(ctx context.Context, day domain.AttendanceDayRow, attendees, absences any, event, rawEvent *string) error {
	stored, err := r.tx.StoredDay(ctx, day.ID)
	if err != nil {
		return err
	}
	if stored == nil {
		return errCalendarRaised
	}
	oldAttendees, err := calendarParse(stored.Attendees)
	if err != nil {
		return err
	}
	oldAbsences, err := calendarParse(stored.ExpectedAbsences)
	if err != nil {
		return err
	}
	changes := map[string][2]string{}
	if before, after := calendarJSONRender(oldAttendees), calendarJSONRender(attendees); before != after {
		changes["attendees"] = [2]string{before, after}
	}
	if before, after := calendarJSONRender(oldAbsences), calendarJSONRender(absences); before != after {
		changes["expected_absences"] = [2]string{before, after}
	}
	if before, after := calendarRef(stored.AssociatedEvent), calendarRef(rawEvent); before != after {
		changes["associated_event"] = [2]string{before, after}
	}
	if err = r.log(ctx, "attendanceday", day.ID, changes, day.CourseAssociation, "attendance_registry.course.sport_association"); err != nil {
		return err
	}
	return r.tx.UpdateDay(ctx, day.ID, attendanceJSON(attendees), attendanceJSON(absences), event)
}

func attendanceJSON(value any) *string {
	if value == nil {
		return nil
	}
	return calendarJSONStore(value)
}

// saveHolding is CarnetSubscription.save() with a changed meta.
func (r *attendanceRun) saveHolding(ctx context.Context, holding domain.AttendanceHolding, meta any) error {
	stored, err := r.tx.StoredHoldingMeta(ctx, holding.ID)
	if err != nil {
		return err
	}
	old, err := calendarParse(stored)
	if err != nil {
		return err
	}
	changes := map[string][2]string{}
	if before, after := calendarJSONRender(old), calendarJSONRender(meta); before != after {
		changes["meta"] = [2]string{before, after}
	}
	if err = r.log(ctx, "carnetsubscription", holding.ID, changes, holding.Association, "subscription.sport_association"); err != nil {
		return err
	}
	return r.tx.UpdateHoldingMeta(ctx, holding.ID, attendanceJSON(meta))
}

// pickCarnet is the views' choice: the enabled holding with the fewest
// lessons left above zero (the first on ties).
func pickCarnet(holdings []domain.AttendanceHolding) (*domain.AttendanceHolding, any, error) {
	var chosen *domain.AttendanceHolding
	var chosenMeta any
	var chosenLeft *big.Rat
	for i := range holdings {
		meta, err := calendarParse(holdings[i].Meta)
		if err != nil {
			return nil, nil, err
		}
		value, err := attendanceIndex(meta, "lessons_left")
		if err != nil {
			return nil, nil, err
		}
		left, ok := attendanceNumber(value)
		if !ok {
			return nil, nil, errCalendarRaised
		}
		if left.Sign() > 0 && (chosen == nil || chosenLeft.Cmp(left) > 0) {
			chosen, chosenMeta, chosenLeft = &holdings[i], meta, left
		}
	}
	return chosen, chosenMeta, nil
}

// consume is `meta['lessons_left'] -= 1; meta['lessons_registry'].append(lesson); save()`.
func (r *attendanceRun) consume(ctx context.Context, holding *domain.AttendanceHolding, meta any, lesson domain.RequestObject) error {
	object := meta.(domain.RequestObject)
	value, _ := object.Lookup("lessons_left")
	left, err := attendanceInteger(value)
	if err != nil {
		return err
	}
	lessons, err := attendanceIndex(object, "lessons_registry")
	if err != nil {
		return err
	}
	list, ok := lessons.([]any)
	if !ok {
		return errCalendarRaised
	}
	updated := dto.PythonCopy(object).(domain.RequestObject)
	updated = dto.PythonSet(updated, "lessons_left", calendarNumberValue(new(big.Int).Sub(left, big.NewInt(1))))
	updated = dto.PythonSet(updated, "lessons_registry", append(dto.PythonCopy(list).([]any), lesson))
	return r.saveHolding(ctx, *holding, updated)
}

func attendanceInt(value int) any { return json.Number(strconv.Itoa(value)) }

func calendarNumberValue(value *big.Int) any { return json.Number(value.String()) }

func attendanceLesson(day domain.AttendanceDayRow, courseID string, courseTitle *string) domain.RequestObject {
	return calendarObject("date", attendanceStrDate(day.Date), "attendance_day_id", day.ID,
		"course", calendarObject("id", courseID, "title", attendanceText(courseTitle)), "title", attendanceText(day.Title))
}

// ---------------------------------------------------------------------------
// attendees (GET course/<uid>/attendees)
// ---------------------------------------------------------------------------

func (s *CourseAttendance) Attendees(ctx context.Context, identity domain.ResolvedIdentity, raw, group string, audit domain.LoginAuditContext) (any, error) {
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return nil, err
	}
	return s.run(ctx, identity, audit, func(r *attendanceRun) (any, error) {
		association, err := r.tx.OwnerAssociation(ctx, identity.ScopeUser.ID, r.scope.Association)
		if err != nil {
			return nil, err
		}
		course, err := r.tx.Course(ctx, id.String(), association, supplierGroup(group), r.scope.Association)
		if err != nil {
			return nil, err
		}
		if course == nil {
			return nil, domain.ErrCalendarCourseNotFound
		}
		registry, err := r.tx.FirstRegistry(ctx, course.ID, r.scope.Association)
		if err != nil {
			return nil, err
		}
		if registry == nil {
			return calendarObject("data", calendarObject("events", []any{})), nil
		}
		if err = r.mapUnlinked(ctx, *registry); err != nil {
			return nil, err
		}
		days, err := r.tx.RegistryDays(ctx, registry.ID, r.scope.Association)
		if err != nil {
			return nil, err
		}
		listed := []any{}
		for _, day := range days {
			item, err := r.listDay(ctx, *registry, day)
			if err != nil {
				return nil, err
			}
			listed = append(listed, item)
		}
		return calendarObject("data", calendarObject("events", listed)), nil
	})
}

func (r *attendanceRun) registryEvents(registry domain.AttendanceRegistryRow) ([]any, error) {
	events, err := calendarParse(registry.Events)
	if err != nil || events == nil {
		return nil, err
	}
	items, ok := calendarItems(events)
	if !ok {
		return nil, errCalendarRaised
	}
	return items, nil
}

// mapUnlinked is map_unlinked_attendance: link the first unlinked day (by
// key) to the first same-hour event no day of the registry has; stop at the
// first day that cannot be linked.
func (r *attendanceRun) mapUnlinked(ctx context.Context, registry domain.AttendanceRegistryRow) error {
	for {
		day, err := r.tx.FirstUnlinkedDay(ctx, registry.ID)
		if err != nil || day == nil {
			return err
		}
		events, err := r.registryEvents(registry)
		if err != nil {
			return err
		}
		linked := false
		for _, event := range events {
			start, err := attendanceEventStart(event)
			if err != nil {
				return err
			}
			if !attendanceSameHour(start, day.Date) {
				continue
			}
			value, err := attendanceIndex(event, "event_id")
			if err != nil {
				return err
			}
			prepared, err := calendarUUID(value)
			if err != nil {
				return err
			}
			if prepared == nil {
				// associated_event=None matches this unlinked day itself.
				continue
			}
			text := value.(string)
			exists, err := r.tx.DayWithEventExists(ctx, registry.ID, *prepared)
			if err != nil {
				return err
			}
			if exists {
				continue
			}
			attendees, err := calendarParse(day.Attendees)
			if err != nil {
				return err
			}
			absences, err := calendarParse(day.ExpectedAbsences)
			if err != nil {
				return err
			}
			if err = r.saveDay(ctx, *day, attendees, absences, prepared, &text); err != nil {
				return err
			}
			linked = true
			break
		}
		if !linked {
			return nil
		}
	}
}

func (r *attendanceRun) listDay(ctx context.Context, registry domain.AttendanceRegistryRow, day domain.AttendanceDayRow) (any, error) {
	attendees, err := calendarParse(day.Attendees)
	if err != nil {
		return nil, err
	}
	var eventAttendees any = []any{}
	if attendees != nil {
		eventAttendees = attendees
	}
	absences, err := calendarParse(day.ExpectedAbsences)
	if err != nil {
		return nil, err
	}
	if absences != nil {
		items, ok := calendarItems(absences)
		if !ok {
			return nil, errCalendarRaised
		}
		for i, item := range items {
			value, err := attendanceIndex(item, attendanceKey)
			if err != nil {
				return nil, err
			}
			prepared, err := calendarUUID(value)
			if err != nil {
				return nil, err
			}
			if prepared == nil {
				continue
			}
			row, err := r.tx.CourseSubscription(ctx, *prepared, false, r.scope)
			if err != nil {
				return nil, err
			}
			if row == nil {
				continue
			}
			name, err := r.name(ctx, row.SubscriptionID)
			if err != nil {
				return nil, err
			}
			absences.([]any)[i] = dto.PythonSet(item.(domain.RequestObject), "associate_name", name)
		}
	} else {
		absences = []any{}
	}
	events, err := r.registryEvents(registry)
	if err != nil {
		return nil, err
	}
	var extended any = domain.RequestObject{}
	var allDay, start, end any = false, nil, nil
	for _, event := range events {
		moment, err := attendanceEventStart(event)
		if err != nil {
			return nil, err
		}
		if !attendanceSameHour(moment, day.Date) {
			continue
		}
		if day.AssociatedEvent != nil {
			value, err := attendanceIndex(event, "event_id")
			if err != nil {
				return nil, err
			}
			if text, ok := value.(string); !ok || text != *day.AssociatedEvent {
				continue
			}
		}
		object := event.(domain.RequestObject)
		if attendanceHas(object, "extendedProps") {
			if extended, err = r.extendedProps(ctx, registry, day, attendees, &absences); err != nil {
				return nil, err
			}
		}
		allDay = attendanceGet(object, "allDay", false)
		start = attendanceGet(object, "start", nil)
		end = attendanceGet(object, "end", nil)
		break
	}
	names := []any{}
	items, ok := calendarItems(absences)
	if !ok {
		return nil, errCalendarRaised
	}
	for _, item := range items {
		if value, present := item.(domain.RequestObject).Lookup("associate_name"); present {
			names = append(names, value)
		} else {
			names = append(names, "<rimosso>")
		}
	}
	return calendarObject("title", attendanceText(day.Title), "allDay", allDay, "extended_props", extended,
		"attendance_day_id", day.ID, "attendance_registry", day.RegistryID, "date", dto.DRFUTCDateTime(day.Date),
		"start", start, "end", end, "attendees", eventAttendees, "expected_absences", names), nil
}

// extendedProps is get_extended_prop_for_attendance_day. Its subscription map
// is keyed by UUID and looked up by str, so attendee names and its
// expected_absences never resolve; an unlinked day is linked to its event and
// saved with the names the view put in expected_absences.
func (r *attendanceRun) extendedProps(ctx context.Context, registry domain.AttendanceRegistryRow, day domain.AttendanceDayRow,
	attendees any, absences *any) (any, error) {
	var eventAttendees any = []any{}
	if attendanceTruthy(attendees) {
		eventAttendees = attendees
	}
	if !attendanceTruthy(*absences) {
		*absences = []any{}
	}
	// set(... ['course_subscription_id']) and the __in lookup's UUID validation.
	invalid := false
	for _, list := range []any{eventAttendees, *absences} {
		items, ok := calendarItems(list)
		if !ok {
			return nil, errCalendarRaised
		}
		for _, item := range items {
			value, err := attendanceIndex(item, attendanceKey)
			if err != nil {
				return nil, err
			}
			if !attendanceHashable(value) {
				return nil, errCalendarRaised
			}
			// Integer ids are valid lookups (the result is unused).
			if _, err := calendarUUID(value); errors.Is(err, errCalendarRaised) {
				invalid = true
			}
		}
	}
	if invalid {
		return nil, errCalendarRaised
	}
	events, err := r.registryEvents(registry)
	if err != nil {
		return nil, err
	}
	var data domain.RequestObject
	found := false
	for _, event := range events {
		moment, err := attendanceEventStart(event)
		if err != nil {
			return nil, err
		}
		if !attendanceSameHour(moment, day.Date) {
			continue
		}
		object := event.(domain.RequestObject)
		if day.AssociatedEvent != nil {
			value, err := attendanceIndex(object, "event_id")
			if err != nil {
				return nil, err
			}
			if text, ok := value.(string); !ok || text != *day.AssociatedEvent {
				continue
			}
		} else if value, present := object.Lookup("event_id"); present {
			// try: event.associated_event = e['event_id']; event.save() except: pass
			text, ok := value.(string)
			if !ok {
				return nil, errCalendarNotPorted
			}
			prepared, err := calendarUUID(text)
			if err != nil {
				return nil, errCalendarNotPorted
			}
			if err = r.saveDay(ctx, day, attendees, *absences, prepared, &text); err != nil {
				return nil, err
			}
		}
		data, found = object, true
		break
	}
	members, err := r.tx.CourseMembers(ctx, day.CourseID)
	if err != nil {
		return nil, err
	}
	potential := []any{}
	now := r.s.now()
	for _, member := range members {
		person, err := r.person(ctx, member.SubscriptionID)
		if err != nil {
			return nil, err
		}
		potential = append(potential, calendarObject("course_subscription_id", member.ID, "subscription", calendarObject(
			"associate", calendarObject("first_name", attendanceText(person.FirstName), "last_name", attendanceText(person.LastName),
				"tax_code", attendanceText(person.TaxCode), "medical_label", dto.CourseSubscriptionMedicalLabel(person.CourseSubscriptionPerson, now)))))
	}
	var props any = domain.RequestObject{}
	if found {
		props = attendanceGet(data, "extendedProps", domain.RequestObject{})
	}
	if props, err = r.instructorLabels(ctx, props); err != nil {
		return nil, err
	}
	allDay, start, end := any(false), any(nil), any(nil)
	if found {
		allDay, start, end = attendanceGet(data, "allDay", false), attendanceGet(data, "start", nil), attendanceGet(data, "end", nil)
	}
	attendeeCount, absenceCount := len(eventAttendees.([]any)), len((*absences).([]any))
	return calendarObject("title", attendanceText(day.Title), "allDay", allDay, "extended_props", props,
		"attendance_day_id", day.ID, "attendance_registry", day.RegistryID, "date", dto.DRFUTCDateTime(day.Date),
		"start", start, "end", end, "course_id", day.CourseID, "total_attendees", attendanceInt(attendeeCount),
		"total_expected_absences", attendanceInt(absenceCount), "potential_attendees", potential,
		"total_expected_total", attendanceInt(len(potential)-absenceCount), "attendees", eventAttendees, "expected_absences", []any{}), nil
}

// instructorLabels adds the instructor's full name to extendedProps.instructor
// (a dict) or to each dict of a list of instructors.
func (r *attendanceRun) instructorLabels(ctx context.Context, props any) (any, error) {
	if !attendanceTruthy(props) {
		return props, nil
	}
	object, ok := props.(domain.RequestObject)
	if !ok {
		return nil, errCalendarNotPorted
	}
	instructor, present := object.Lookup("instructor")
	if !present {
		return props, nil
	}
	label := func(item domain.RequestObject) (domain.RequestObject, error) {
		value, _ := item.Lookup("instructor_id")
		prepared, err := calendarUUID(value)
		if err != nil || prepared == nil {
			return item, err
		}
		person, err := r.tx.InstructorName(ctx, *prepared)
		if err != nil || person == nil {
			return item, err
		}
		return dto.PythonSet(dto.PythonCopy(item).(domain.RequestObject), "label", attendanceFullName(person.FirstName, person.LastName)), nil
	}
	switch v := instructor.(type) {
	case domain.RequestObject:
		if !attendanceHas(v, "instructor_id") {
			return props, nil
		}
		updated, err := label(v)
		if err != nil {
			return nil, err
		}
		return dto.PythonSet(dto.PythonCopy(object).(domain.RequestObject), "instructor", updated), nil
	case []any:
		list := dto.PythonCopy(v).([]any)
		for i, item := range list {
			switch entry := item.(type) {
			case domain.RequestObject:
				if attendanceHas(entry, "instructor_id") {
					updated, err := label(entry)
					if err != nil {
						return nil, err
					}
					list[i] = updated
				}
			default:
				return nil, errCalendarNotPorted
			}
		}
		return dto.PythonSet(dto.PythonCopy(object).(domain.RequestObject), "instructor", list), nil
	}
	if attendanceTruthy(instructor) {
		return nil, errCalendarNotPorted
	}
	return props, nil
}

// ---------------------------------------------------------------------------
// attendees_update (POST course/<uid>/attendees/<attendance_day_uid>/update)
// ---------------------------------------------------------------------------

func (s *CourseAttendance) AttendeesUpdate(ctx context.Context, identity domain.ResolvedIdentity, raw, rawDay, group string, body any,
	bodyErr error, audit domain.LoginAuditContext) (any, error) {
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return nil, err
	}
	dayID, err := paymentInfoParseUUID(rawDay)
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
	keys := map[string]bool{}
	for _, field := range data {
		keys[field.Name] = true
	}
	newValue, present := data.Lookup("attendees")
	if !present || len(keys) != 1 {
		return nil, &domain.CalendarValidation{Detail: "Missing or wrong events data."}
	}
	return s.run(ctx, identity, audit, func(r *attendanceRun) (any, error) {
		association, err := r.tx.OwnerAssociation(ctx, identity.ScopeUser.ID, r.scope.Association)
		if err != nil {
			return nil, err
		}
		course, err := r.tx.Course(ctx, id.String(), association, supplierGroup(group), r.scope.Association)
		if err != nil {
			return nil, err
		}
		if course == nil {
			return nil, domain.ErrCalendarCourseNotFound
		}
		registry, err := r.tx.FirstRegistry(ctx, course.ID, r.scope.Association)
		if err != nil {
			return nil, err
		}
		if registry == nil {
			return nil, attendanceAnswer(404, "detail", "Course Attendance Registry not found.")
		}
		day, err := r.tx.Day(ctx, dayID.String(), &registry.ID, r.scope.Association)
		if err != nil {
			return nil, err
		}
		if day == nil {
			return nil, attendanceAnswer(404, "detail", "Course Event day not found.")
		}
		old, err := calendarParse(day.Attendees)
		if err != nil {
			return nil, err
		}
		if old == nil {
			old = []any{}
		}
		if err = r.returnLessons(ctx, *course, *day, old, newValue); err != nil {
			return nil, err
		}
		items, ok := calendarItems(newValue)
		if !ok {
			return nil, errCalendarRaised
		}
		for _, attendee := range items {
			listed, err := attendanceIn(old, attendee)
			if err != nil {
				return nil, err
			}
			if listed {
				continue
			}
			value, err := attendanceIndex(attendee, attendanceKey)
			if err != nil {
				return nil, err
			}
			prepared, err := calendarUUID(value)
			if err != nil {
				return nil, err
			}
			if prepared == nil {
				continue
			}
			row, err := r.tx.CourseSubscription(ctx, *prepared, false, r.scope)
			if err != nil {
				return nil, err
			}
			if row == nil {
				continue
			}
			holdings, err := r.tx.Holdings(ctx, row.ID, row.SubscriptionID, r.scope.Association)
			if err != nil {
				return nil, err
			}
			chosen, meta, err := pickCarnet(holdings)
			if err != nil {
				return nil, err
			}
			if chosen != nil {
				if chosen.PaymentID != nil && chosen.PaymentPaid != nil && !*chosen.PaymentPaid {
					return nil, attendanceAnswer(412, "msg", "Il carnet non è ancora stato pagato.")
				}
				if err = r.consume(ctx, chosen, meta, attendanceLesson(*day, course.ID, course.Title)); err != nil {
					return nil, err
				}
			} else if len(holdings) > 0 {
				name, err := r.name(ctx, row.SubscriptionID)
				if err != nil {
					return nil, err
				}
				return nil, attendanceAnswer(412, "message", "Carnet is already finished.", "associate_name", name)
			}
		}
		absences, err := calendarParse(day.ExpectedAbsences)
		if err != nil {
			return nil, err
		}
		if err = r.saveDay(ctx, *day, newValue, absences, day.AssociatedEvent, day.AssociatedEvent); err != nil {
			return nil, err
		}
		return calendarObject("message", "Attendance Registry updated."), nil
	})
}

// returnLessons gives back, for each removed attendee, the lesson of the
// newest carnet whose lesson matches the course and the day's date.
func (r *attendanceRun) returnLessons(ctx context.Context, course domain.AttendanceCourse, day domain.AttendanceDayRow, old, newValue any) error {
	items, ok := calendarItems(old)
	if !ok {
		return errCalendarRaised
	}
	dayDate := day.Date.UTC()
	dayText := fmt.Sprintf("%02d/%02d/%d", dayDate.Day(), int(dayDate.Month()), dayDate.Year())
	for _, attendee := range items {
		kept, err := attendanceIn(newValue, attendee)
		if err != nil {
			return err
		}
		if kept {
			continue
		}
		value, err := attendanceIndex(attendee, attendanceKey)
		if err != nil {
			return err
		}
		prepared, err := calendarUUID(value)
		if err != nil {
			return err
		}
		var row *domain.AttendanceCourseSubscription
		if prepared != nil {
			if row, err = r.tx.CourseSubscription(ctx, *prepared, true, r.scope); err != nil {
				return err
			}
		}
		if row == nil {
			return errCalendarRaised
		}
		holdings, err := r.tx.Holdings(ctx, row.ID, row.SubscriptionID, r.scope.Association)
		if err != nil {
			return err
		}
		for _, holding := range holdings {
			meta, err := calendarParse(holding.Meta)
			if err != nil {
				return err
			}
			registryValue, err := attendanceIndex(meta, "lessons_registry")
			if err != nil {
				return err
			}
			lessons, ok := calendarItems(registryValue)
			if !ok {
				return errCalendarRaised
			}
			found := false
			for i, item := range lessons {
				lesson, ok := item.(domain.RequestObject)
				if !ok {
					return errCalendarRaised
				}
				if !attendanceHas(lesson, "attendance_day_id") {
					lesson = append(lesson, domain.RequestField{Name: "attendance_day_id", Value: nil})
					lessons[i] = lesson
				}
				courseValue, err := attendanceIndex(lesson, "course")
				if err != nil {
					return err
				}
				courseID, err := attendanceIndex(courseValue, "id")
				if err != nil {
					return err
				}
				if text, ok := courseID.(string); !ok || text != course.ID {
					continue
				}
				dateValue, err := attendanceIndex(lesson, "date")
				if err != nil {
					return err
				}
				moment, err := dto.AuditFromISOFormat(dateValue)
				if errors.Is(err, dto.ErrAuditNotPorted) {
					return errCalendarNotPorted
				}
				if err != nil {
					return errCalendarRaised
				}
				// lesson['attendance_day_id'] == event_day.attendance_day_id compares str with UUID: never equal.
				if moment.Date != dayText {
					continue
				}
				remaining := append(append([]any{}, lessons[:i]...), lessons[i+1:]...)
				object := meta.(domain.RequestObject)
				object = dto.PythonSet(object, "lessons_registry", remaining)
				leftValue, err := attendanceIndex(object, "lessons_left")
				if err != nil {
					return err
				}
				left, err := attendanceInteger(leftValue)
				if err != nil {
					return err
				}
				object = dto.PythonSet(object, "lessons_left", calendarNumberValue(new(big.Int).Add(left, big.NewInt(1))))
				if err = r.saveHolding(ctx, holding, object); err != nil {
					return err
				}
				found = true
				break
			}
			if found {
				break
			}
		}
	}
	return nil
}

// ---------------------------------------------------------------------------
// subscription_attendance (GET subscription/<uid>/attendance)
// ---------------------------------------------------------------------------

func (s *CourseAttendance) SubscriptionAttendance(ctx context.Context, identity domain.ResolvedIdentity, raw string, course *string,
	group string) (any, error) {
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return nil, err
	}
	return s.run(ctx, identity, domain.LoginAuditContext{}, func(r *attendanceRun) (any, error) {
		var subscription *string
		groupID := supplierGroup(group)
		if identity.ScopeUser.Role == domain.RoleAthlete {
			subscription, err = r.tx.Subscription(ctx, id.String(), nil, &identity.ScopeUser.ID, groupID, r.scope)
		} else {
			association, err := r.tx.OwnerAssociation(ctx, identity.ScopeUser.ID, r.scope.Association)
			if err != nil {
				return nil, err
			}
			subscription, err = r.tx.Subscription(ctx, id.String(), &association, nil, groupID, r.scope)
			if err != nil {
				return nil, err
			}
		}
		if err != nil {
			return nil, err
		}
		var selected *string
		if course != nil && *course != "all" {
			parsed, err := paymentInfoParseUUID(*course)
			if err != nil {
				return nil, errCalendarRaised
			}
			text := parsed.String()
			selected = &text
		}
		rows, err := r.tx.SubscriptionCourses(ctx, subscription, selected, true, r.scope)
		if err != nil {
			return nil, err
		}
		courses, detailed, members := []string{}, []any{}, map[string]bool{}
		for _, row := range rows {
			members[row.ID] = true
			seen := false
			for _, known := range courses {
				seen = seen || known == row.CourseID
			}
			if !seen {
				courses = append(courses, row.CourseID)
				detailed = append(detailed, calendarObject("course_id", row.CourseID, "title", attendanceText(row.CourseTitle)))
			}
		}
		days := []domain.AttendanceDayRow{}
		if len(courses) > 0 {
			if days, err = r.tx.DaysOfRegistries(ctx, courses, r.scope.Association); err != nil {
				return nil, err
			}
		}
		attended := []domain.AttendanceDayRow{}
		for _, day := range days {
			attendees, err := calendarParse(day.Attendees)
			if err != nil {
				return nil, err
			}
			if !attendanceTruthy(attendees) {
				continue
			}
			items, ok := calendarItems(attendees)
			if !ok {
				return nil, errCalendarRaised
			}
			for _, item := range items {
				value, err := attendanceIndex(item, attendanceKey)
				if err != nil {
					return nil, err
				}
				if text, ok := value.(string); ok && members[text] {
					attended = append(attended, day)
				}
			}
		}
		since := s.now().Add(-30 * 24 * time.Hour)
		listed, recent := []any{}, 0
		for _, day := range attended {
			listed = append(listed, calendarObject("title", attendanceText(day.Title), "attendance_day_id", day.ID,
				"attendance_registry", day.RegistryID, "course", calendarObject("course_id", day.CourseID, "title", attendanceText(day.CourseTitle)),
				"date", dto.DRFUTCDateTime(day.Date)))
			if !day.Date.Before(since) {
				recent++
			}
		}
		return calendarObject("data", calendarObject("attendance_days", listed, "courses", detailed, "stats", calendarObject(
			"total_attendance_last_30_days", attendanceInt(recent), "total_absences_last_30_days", attendanceInt(0),
			"total_attendance", attendanceInt(len(attended)), "total_absences", attendanceInt(0)))), nil
	})
}

// ---------------------------------------------------------------------------
// attendance_mark (POST attendance/<uid>/mark)
// ---------------------------------------------------------------------------

func (s *CourseAttendance) Mark(ctx context.Context, identity domain.ResolvedIdentity, raw, group string, body any, bodyErr error,
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
	selected, _ := data.Lookup("attendance_day_id")
	return s.run(ctx, identity, audit, func(r *attendanceRun) (any, error) {
		subscription, err := r.tx.Subscription(ctx, id.String(), nil, nil, supplierGroup(group), r.scope)
		if err != nil {
			return nil, err
		}
		if subscription == nil {
			return nil, attendanceAnswer(404, "msg", "Iscrizione non trovata.")
		}
		today := s.now().In(attendanceRome).Format("2006-01-02")
		available := []domain.AttendanceDayRow{}
		already := false
		var courseSub *domain.AttendanceCourseSubscription
		if selected == nil {
			rows, err := r.tx.SubscriptionCourses(ctx, subscription, nil, false, r.scope)
			if err != nil {
				return nil, err
			}
			for i := range rows {
				courseSub = &rows[i]
				registry, err := r.tx.FirstRegistry(ctx, rows[i].CourseID, r.scope.Association)
				if err != nil {
					return nil, err
				}
				if registry == nil {
					continue
				}
				day, err := r.tx.TodayDay(ctx, registry.ID, today, r.scope.Association)
				if err != nil {
					return nil, err
				}
				if day == nil {
					continue
				}
				attendees, err := calendarParse(day.Attendees)
				if err != nil {
					return nil, err
				}
				if attendanceTruthy(attendees) {
					hit, err := attendanceListed(attendees, rows[i].ID)
					if err != nil {
						return nil, err
					}
					if hit {
						already = true
						continue
					}
				}
				available = append(available, *day)
			}
		} else {
			prepared, err := calendarUUID(selected)
			if err != nil {
				return nil, err
			}
			var day *domain.AttendanceDayRow
			if prepared != nil {
				if day, err = r.tx.Day(ctx, *prepared, nil, r.scope.Association); err != nil {
					return nil, err
				}
			}
			if day == nil {
				return nil, attendanceAnswer(404, "msg", "Giorno di presenza non trovato.")
			}
			attendees, err := calendarParse(day.Attendees)
			if err != nil {
				return nil, err
			}
			if attendanceTruthy(attendees) {
				// str(subscription.course_subscription_id): Subscription has no such attribute.
				return nil, errCalendarRaised
			}
			available = append(available, *day)
			if courseSub, err = r.tx.CourseSubscriptionFor(ctx, day.CourseID, *subscription, r.scope); err != nil {
				return nil, err
			}
		}
		name := func() (string, error) { return r.name(ctx, subscription) }
		switch len(available) {
		case 0:
			full, err := name()
			if err != nil {
				return nil, err
			}
			if already {
				return nil, attendanceAnswer(200, "msg", "Ciao "+full+", hai già effettuato il check-in.")
			}
			return nil, attendanceAnswer(404, "msg", "Ciao "+full+", non hai corsi oggi.")
		case 1:
			return r.checkIn(ctx, available[0], courseSub, name)
		}
		choices := []any{}
		for _, day := range available {
			choices = append(choices, calendarObject("attendance_day_id", day.ID, "date", dto.DRFUTCDateTime(day.Date),
				"course", calendarObject("course_id", day.CourseID, "title", attendanceText(day.CourseTitle))))
		}
		return nil, attendanceAnswer(207, "msg", "Seleziona il corso per il check-in.", "data", choices)
	})
}

// attendanceListed is any(str(id) == attendee['course_subscription_id'] ...).
func attendanceListed(attendees any, id string) (bool, error) {
	items, ok := calendarItems(attendees)
	if !ok {
		return false, errCalendarRaised
	}
	for _, item := range items {
		value, err := attendanceIndex(item, attendanceKey)
		if err != nil {
			return false, err
		}
		if text, ok := value.(string); ok && text == id {
			return true, nil
		}
	}
	return false, nil
}

func (r *attendanceRun) checkIn(ctx context.Context, day domain.AttendanceDayRow, courseSub *domain.AttendanceCourseSubscription,
	name func() (string, error)) (any, error) {
	attendees, err := calendarParse(day.Attendees)
	if err != nil {
		return nil, err
	}
	if attendees == nil {
		attendees = []any{}
	}
	if courseSub == nil {
		return nil, errCalendarRaised
	}
	holdings, err := r.tx.Holdings(ctx, courseSub.ID, courseSub.SubscriptionID, r.scope.Association)
	if err != nil {
		return nil, err
	}
	chosen, meta, err := pickCarnet(holdings)
	if err != nil {
		return nil, err
	}
	if chosen != nil {
		if chosen.PaymentID != nil && chosen.PaymentPaid != nil && !*chosen.PaymentPaid {
			full, err := name()
			if err != nil {
				return nil, err
			}
			return nil, attendanceAnswer(412, "msg", "Caro "+full+", non hai ancora pagato il carnet.")
		}
		if err = r.consume(ctx, chosen, meta, attendanceLesson(day, courseSub.CourseID, courseSub.CourseTitle)); err != nil {
			return nil, err
		}
	} else if len(holdings) > 0 {
		full, err := name()
		if err != nil {
			return nil, err
		}
		return nil, attendanceAnswer(412, "msg", "Caro "+full+", hai finito il carnet.")
	}
	list, ok := attendees.([]any)
	if !ok {
		return nil, errCalendarRaised
	}
	list = append(append([]any{}, list...), calendarObject(attendanceKey, courseSub.ID))
	absences, err := calendarParse(day.ExpectedAbsences)
	if err != nil {
		return nil, err
	}
	if err = r.saveDay(ctx, day, list, absences, day.AssociatedEvent, day.AssociatedEvent); err != nil {
		return nil, err
	}
	full, err := name()
	if err != nil {
		return nil, err
	}
	return calendarObject("msg", "Check-in effettuato! Ciao "+full+"."), nil
}
