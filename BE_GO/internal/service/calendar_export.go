package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pydatetime"
)

// CalendarExport ports full_events_calendar_export (attendee_views.py): the
// association's registries rendered by icalendar 6.1.1 as a bare VCALENDAR.
type CalendarExport struct {
	store domain.CalendarStore
	now   func() time.Time
}

func NewCalendarExport(store domain.CalendarStore, now func() time.Time) *CalendarExport {
	return &CalendarExport{store: store, now: now}
}

// Export answers the calendar bytes. courseID is the course_id parameter when
// present.
func (s *CalendarExport) Export(ctx context.Context, identity domain.ResolvedIdentity, courseID *string, group string) ([]byte, error) {
	var course *string
	if courseID != nil {
		id, err := paymentInfoParseUUID(*courseID)
		if err != nil {
			return nil, err
		}
		text := id.String()
		course = &text
	}
	if identity.ScopeUser.Role == domain.RoleAthlete {
		// IsAssociationUser answers athletes before the view; the view's own
		// branch is not reachable.
		return nil, domain.ErrMiscRoutesServer
	}
	tx, err := s.store.BeginCalendar(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	association, err := tx.OwnerAssociation(ctx, identity.ScopeUser.ID, nil, false)
	if errors.Is(err, domain.ErrAssociationNotFound) {
		return nil, domain.ErrMiscRoutesServer
	}
	if err != nil {
		return nil, err
	}
	scope := paymentCategoryAssociationScope(identity)
	courses, err := tx.CourseIDs(ctx, association, supplierGroup(group), scope)
	if err != nil {
		return nil, err
	}
	if course != nil {
		narrowed := []string{}
		for _, id := range courses {
			if id == *course {
				narrowed = append(narrowed, id)
			}
		}
		courses = narrowed
	}
	registries := []domain.CalendarRegistry{}
	if len(courses) > 0 {
		if registries, err = tx.Registries(ctx, courses, scope); err != nil {
			return nil, err
		}
	}
	var out strings.Builder
	out.WriteString("BEGIN:VCALENDAR\r\n")
	for _, registry := range registries {
		if registry.Events == nil {
			continue
		}
		parsed, err := dto.ParseProfileTablesJSON([]byte(*registry.Events))
		if err != nil {
			return nil, err
		}
		events, err := calendarExportEvents(parsed)
		if err != nil {
			return nil, err
		}
		for _, event := range events {
			if err = s.writeEvent(&out, event); err != nil {
				return nil, err
			}
		}
	}
	out.WriteString("END:VCALENDAR\r\n")
	return []byte(out.String()), nil
}

// calendarExportEvents is `if registry.events: for event in registry.events`.
func calendarExportEvents(value any) ([]any, error) {
	switch v := value.(type) {
	case nil:
		return nil, nil
	case bool:
		if !v {
			return nil, nil
		}
	case json.Number:
		if number, err := strconv.ParseFloat(string(v), 64); err == nil && number == 0 {
			return nil, nil
		}
	case string:
		if v == "" {
			return nil, nil
		}
		// Iterating a string yields characters: event['title'] raises.
	case domain.RequestObject:
		if len(v) == 0 {
			return nil, nil
		}
		// Iterating a dict yields its keys: event['title'] raises.
	case []any:
		return v, nil
	}
	return nil, domain.ErrMiscRoutesServer
}

func (s *CalendarExport) writeEvent(out *strings.Builder, raw any) error {
	event, ok := raw.(domain.RequestObject)
	if !ok {
		return domain.ErrMiscRoutesServer // event['title'] on a non-dict
	}
	title, present := event.Lookup("title")
	if !present {
		return domain.ErrMiscRoutesServer
	}
	summary, err := calendarExportTexts(title)
	if err != nil {
		return err
	}
	startRaw, present := event.Lookup("start")
	if !present {
		return domain.ErrMiscRoutesServer
	}
	start, err := calendarExportParse(startRaw)
	if err != nil {
		return err
	}
	endRaw, present := event.Lookup("end")
	if !present {
		return domain.ErrMiscRoutesServer
	}
	var end *calendarExportMoment
	if text, isText := endRaw.(string); endRaw != nil && !(isText && text == "None") {
		if end, err = calendarExportParse(endRaw); err != nil {
			return err
		}
	} else {
		if start == nil {
			return domain.ErrMiscRoutesServer // None + timedelta
		}
		later := *start
		later.at = later.at.Add(time.Hour)
		if later.at.Year() > 9999 {
			return domain.ErrMiscRoutesServer // OverflowError
		}
		end = &later
	}
	if start == nil || end == nil {
		return domain.ErrMiscRoutesServer // add('dtstart'/'dtend', None) raises
	}
	stamp := s.now().UTC()
	eventID, present := event.Lookup("event_id")
	if !present {
		return domain.ErrMiscRoutesServer
	}
	uid, err := calendarExportTexts(eventID)
	if err != nil {
		return err
	}
	var description *string
	if props, present := event.Lookup("extendedProps"); present {
		has, err := calendarExportContains(props, "instructor")
		if err != nil {
			return err
		}
		if has {
			object, isObject := props.(domain.RequestObject)
			if !isObject {
				return domain.ErrMiscRoutesServer // props['instructor'] on a string or list
			}
			instructor, _ := object.Lookup("instructor")
			if fields, isObject := instructor.(domain.RequestObject); isObject {
				if label, present := fields.Lookup("label"); present {
					value := "Instructor: " + dto.PythonStr(label)
					description = &value
				}
			}
		}
	}
	lines := []string{"BEGIN:VEVENT"}
	for _, text := range summary {
		lines = append(lines, "SUMMARY:"+calendarExportEscape(text))
	}
	lines = append(lines, "DTSTART"+start.render(), "DTEND"+end.render(), "DTSTAMP:"+stamp.Format("20060102T150405")+"Z")
	for _, text := range uid {
		lines = append(lines, "UID:"+calendarExportEscape(text))
	}
	if description != nil {
		lines = append(lines, "DESCRIPTION:"+calendarExportEscape(*description))
	}
	lines = append(lines, "END:VEVENT")
	for _, line := range lines {
		out.WriteString(calendarExportFold(line))
		out.WriteString("\r\n")
	}
	return nil
}

// calendarExportContains is Python's `name in value` for the JSON value.
func calendarExportContains(value any, name string) (bool, error) {
	switch v := value.(type) {
	case domain.RequestObject:
		_, present := v.Lookup(name)
		return present, nil
	case string:
		return strings.Contains(v, name), nil
	case []any:
		for _, item := range v {
			if text, isText := item.(string); isText && text == name {
				return true, nil
			}
		}
		return false, nil
	}
	return false, domain.ErrMiscRoutesServer // `in` on None, a number or a bool
}

// calendarExportTexts is the content lines' values for a JSON value icalendar
// receives: a list becomes one property line per item (an empty list none),
// each item vText(str(item)); a scalar, dict or nested value is str() of it
// (a float as Python's repr, containers as their repr).
func calendarExportTexts(value any) ([]string, error) {
	items, isList := value.([]any)
	if !isList {
		return []string{dto.PythonStr(value)}, nil
	}
	texts := make([]string, len(items))
	for i, item := range items {
		texts[i] = dto.PythonStr(item)
	}
	return texts, nil
}

// calendarExportEscape is icalendar's escape_char (order matters).
func calendarExportEscape(text string) string {
	text = strings.ReplaceAll(text, `\N`, "\n")
	text = strings.ReplaceAll(text, `\`, `\\`)
	text = strings.ReplaceAll(text, ";", `\;`)
	text = strings.ReplaceAll(text, ",", `\,`)
	text = strings.ReplaceAll(text, "\r\n", `\n`)
	return strings.ReplaceAll(text, "\n", `\n`)
}

// calendarExportFold is icalendar's foldline: a CRLF and a space before the
// character that brings the line to 75 octets.
func calendarExportFold(line string) string {
	var out strings.Builder
	size := 0
	for _, char := range line {
		width := utf8.RuneLen(char)
		size += width
		if size >= 75 {
			out.WriteString("\r\n ")
			size = width
		}
		out.WriteRune(char)
	}
	return out.String()
}

type calendarExportMoment struct {
	at   time.Time // wall clock
	utc  bool
	zone string // TZID of a fixed offset other than zero
}

// render is the property's parameters and value: ":<local time>" with a Z for
// UTC, or ";TZID=<name>:<local time>" (the name quoted when it holds a colon).
func (m calendarExportMoment) render() string {
	text := fmt.Sprintf("%04d%02d%02dT%02d%02d%02d", m.at.Year(), int(m.at.Month()), m.at.Day(), m.at.Hour(), m.at.Minute(), m.at.Second())
	if m.utc {
		return ":" + text + "Z"
	}
	if m.zone != "" {
		name := m.zone
		if strings.ContainsAny(name, ",;: ’'") {
			name = `"` + name + `"`
		}
		return ";TZID=" + name + ":" + text
	}
	return ":" + text
}

// calendarExportParse is django.utils.dateparse.parse_datetime: nil (no
// result) when Django returns None, domain.ErrMiscRoutesServer when it raises
// (a non-str value is a TypeError, a shaped-but-invalid text a ValueError).
func calendarExportParse(value any) (*calendarExportMoment, error) {
	text, isText := value.(string)
	if !isText {
		return nil, domain.ErrMiscRoutesServer // fromisoformat(non-str) raises TypeError
	}
	parsed, err := pydatetime.ParseDatetime(text)
	if err != nil {
		return nil, domain.ErrMiscRoutesServer
	}
	if parsed == nil {
		return nil, nil
	}
	return calendarExportFromMoment(*parsed)
}

// calendarExportFromMoment names the timezone of a parsed value the way
// icalendar does: UTC for a zero offset, the first identifier of its table for
// an offset of whole minutes it lists, else the tzinfo's own name. Offsets
// that leave the table by whole minutes are the timezone's own name.
func calendarExportFromMoment(parsed pydatetime.Moment) (*calendarExportMoment, error) {
	moment := &calendarExportMoment{at: parsed.Wall()}
	if !parsed.Aware {
		return moment, nil
	}
	if parsed.Offset == 0 {
		moment.utc = true
		return moment, nil
	}
	if parsed.Offset%60000000 == 0 {
		if zone, known := calendarExportZones[int(parsed.Offset/60000000)]; known {
			moment.zone = zone
			return moment, nil
		}
	}
	moment.zone = parsed.ZoneName()
	return moment, nil
}
