package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
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
	summary, err := calendarExportText(title)
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
	uid, err := calendarExportText(eventID)
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
					text, err := calendarExportText(label)
					if err != nil {
						return err
					}
					value := "Instructor: " + text
					description = &value
				}
			}
		}
	}
	lines := []string{"BEGIN:VEVENT", "SUMMARY:" + calendarExportEscape(summary), "DTSTART" + start.render(), "DTEND" + end.render(),
		"DTSTAMP:" + stamp.Format("20060102T150405") + "Z", "UID:" + calendarExportEscape(uid)}
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

// calendarExportText is str() of a JSON scalar as icalendar's vText receives
// it (a float as Python's repr). Lists (one property per item) and dicts
// (repr) are refused.
func calendarExportText(value any) (string, error) {
	switch v := value.(type) {
	case nil:
		return "None", nil
	case bool:
		if v {
			return "True", nil
		}
		return "False", nil
	case string:
		return v, nil
	case json.Number:
		text := string(v)
		if strings.ContainsAny(text, ".eE") {
			return dto.PythonStr(v), nil
		}
		return text, nil
	}
	return "", domain.ErrMiscRoutesNotPorted
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

// calendarExportOffset reads a UTC offset written ±HH, ±HHMM or ±HH:MM.
// ok=false for another form (seconds, minutes past 59).
func calendarExportOffset(zone string) (minutes int, ok bool) {
	if len(zone) < 3 || (zone[0] != '+' && zone[0] != '-') {
		return 0, false
	}
	digits := zone[1:]
	if len(digits) == 5 && digits[2] == ':' {
		digits = digits[:2] + digits[3:]
	}
	if len(digits) != 2 && len(digits) != 4 {
		return 0, false
	}
	hours, valid := calendarExportDigits(digits[:2])
	if !valid {
		return 0, false
	}
	if len(digits) == 4 {
		if minutes, valid = calendarExportDigits(digits[2:]); !valid || minutes > 59 {
			return 0, false
		}
	}
	minutes += hours * 60
	if zone[0] == '-' {
		minutes = -minutes
	}
	return minutes, true
}

// calendarExportZoned names the timezone of a parsed offset. named is the
// timezone's own name (datetime.timezone: "UTC+05:45"), used when icalendar's
// table lacks the offset; without it such an offset is refused. An offset of
// 24 hours or more is no timezone (ValueError).
func calendarExportZoned(moment *calendarExportMoment, minutes int, named bool) (*calendarExportMoment, error) {
	if moment == nil {
		return nil, nil
	}
	if minutes == 0 {
		moment.utc = true
		return moment, nil
	}
	size := minutes
	if size < 0 {
		size = -size
	}
	if size >= 24*60 {
		return nil, domain.ErrMiscRoutesServer
	}
	if zone, known := calendarExportZones[minutes]; known {
		moment.zone = zone
		return moment, nil
	}
	if !named {
		return nil, domain.ErrMiscRoutesNotPorted
	}
	sign := "+"
	if minutes < 0 {
		sign = "-"
	}
	moment.zone = fmt.Sprintf("UTC%s%02d:%02d", sign, size/60, size%60)
	return moment, nil
}

// calendarExportParse is django.utils.dateparse.parse_datetime for the forms
// Go reproduces: nil (no result) when Django returns None, an error when it
// raises or for a form outside the ported subset. Only naive and zero-offset
// values are ported (icalendar names other fixed offsets with a TZID).
func calendarExportParse(value any) (*calendarExportMoment, error) {
	text, isText := value.(string)
	if !isText {
		return nil, domain.ErrMiscRoutesServer // fromisoformat(non-str) raises TypeError
	}
	for _, char := range text {
		if char > 0x7f {
			return nil, domain.ErrMiscRoutesNotPorted
		}
	}
	if moment, ok, err := calendarExportISO(text); ok || err != nil {
		return moment, err
	}
	return calendarExportRegex(text)
}

func calendarExportDigits(text string) (int, bool) {
	if text == "" {
		return 0, false
	}
	for _, char := range text {
		if char < '0' || char > '9' {
			return 0, false
		}
	}
	value, err := strconv.Atoi(text)
	return value, err == nil
}

// calendarExportBuild is datetime(...): an invalid component raises.
func calendarExportBuild(year, month, day, hour, minute, second, micro int, utc bool) (*calendarExportMoment, error) {
	if year < 1 || month < 1 || month > 12 || day < 1 || hour > 23 || minute > 59 || second > 59 {
		return nil, domain.ErrMiscRoutesServer
	}
	at := time.Date(year, time.Month(month), day, hour, minute, second, micro*1000, time.UTC)
	if at.Day() != day {
		return nil, domain.ErrMiscRoutesServer
	}
	return &calendarExportMoment{at: at, utc: utc}, nil
}

// calendarExportISO is the ported subset of datetime.fromisoformat: ok=false
// means fromisoformat raises ValueError (the regex fallback runs); an error
// refuses a form outside the subset.
func calendarExportISO(text string) (*calendarExportMoment, bool, error) {
	var year, month, day int
	var rest string
	switch {
	case len(text) >= 10 && text[4] == '-' && text[7] == '-':
		y, ok1 := calendarExportDigits(text[0:4])
		m, ok2 := calendarExportDigits(text[5:7])
		d, ok3 := calendarExportDigits(text[8:10])
		if !(ok1 && ok2 && ok3) {
			if len(text) > 5 && text[5] == 'W' {
				return nil, false, domain.ErrMiscRoutesNotPorted
			}
			return nil, false, nil
		}
		year, month, day, rest = y, m, d, text[10:]
	case len(text) >= 8 && text[4] != '-':
		y, ok1 := calendarExportDigits(text[0:4])
		m, ok2 := calendarExportDigits(text[4:6])
		d, ok3 := calendarExportDigits(text[6:8])
		if !(ok1 && ok2 && ok3) {
			if len(text) > 4 && text[4] == 'W' {
				return nil, false, domain.ErrMiscRoutesNotPorted
			}
			return nil, false, nil
		}
		year, month, day, rest = y, m, d, text[8:]
	default:
		return nil, false, nil
	}
	if rest == "" {
		moment, err := calendarExportBuild(year, month, day, 0, 0, 0, 0, false)
		if err != nil {
			return nil, false, nil // ValueError: the regex (which needs a time) answers None
		}
		return moment, true, nil
	}
	if rest[0] != 'T' && rest[0] != ' ' {
		// fromisoformat accepts any single separator character.
		return nil, false, domain.ErrMiscRoutesNotPorted
	}
	clock := rest[1:]
	utc, offset := false, 0
	if index := strings.IndexAny(clock, "+-Z"); index >= 0 {
		zone := clock[index:]
		clock = clock[:index]
		if zone == "Z" {
			utc = true
		} else {
			minutes, known := calendarExportOffset(zone)
			if !known {
				return nil, false, domain.ErrMiscRoutesNotPorted
			}
			if minutes <= -24*60 || minutes >= 24*60 {
				// fromisoformat's ValueError; the regex raises on the same offset or answers None.
				return nil, false, domain.ErrMiscRoutesServer
			}
			utc, offset = minutes == 0, minutes
		}
	}
	hour, minute, second, micro, ok := calendarExportClock(clock)
	if !ok {
		// Outside the ported clock forms: the regex fallback (for example
		// single-digit hours) answers, else the input is refused.
		moment, err := calendarExportRegex(text)
		if moment == nil && err == nil {
			err = domain.ErrMiscRoutesNotPorted
		}
		return moment, true, err
	}
	moment, err := calendarExportBuild(year, month, day, hour, minute, second, micro, utc)
	if err != nil {
		// fromisoformat's ValueError; the regex would raise on the same values.
		return nil, false, err
	}
	if offset != 0 {
		if moment, err = calendarExportZoned(moment, offset, true); err != nil {
			return nil, false, err
		}
	}
	return moment, true, nil
}

// calendarExportClock parses HH[:MM[:SS[.f]]] or HHMM[SS[.f]] (',' or '.').
func calendarExportClock(clock string) (hour, minute, second, micro int, ok bool) {
	fraction := ""
	if index := strings.IndexAny(clock, ".,"); index >= 0 {
		fraction = clock[index+1:]
		clock = clock[:index]
		if _, valid := calendarExportDigits(fraction); !valid {
			return 0, 0, 0, 0, false
		}
	}
	var parts []string
	if strings.Contains(clock, ":") {
		parts = strings.Split(clock, ":")
	} else {
		for i := 0; i < len(clock); i += 2 {
			if i+2 > len(clock) {
				return 0, 0, 0, 0, false
			}
			parts = append(parts, clock[i:i+2])
		}
	}
	if len(parts) == 0 || len(parts) > 3 || (fraction != "" && len(parts) != 3) {
		return 0, 0, 0, 0, false
	}
	values := [3]int{}
	for i, part := range parts {
		if len(part) != 2 {
			return 0, 0, 0, 0, false
		}
		value, valid := calendarExportDigits(part)
		if !valid {
			return 0, 0, 0, 0, false
		}
		values[i] = value
	}
	if fraction != "" {
		if len(fraction) > 6 {
			fraction = fraction[:6]
		}
		micro, _ = strconv.Atoi(fraction + strings.Repeat("0", 6-len(fraction)))
	}
	return values[0], values[1], values[2], micro, true
}

// calendarExportPattern is dateparse.datetime_re (ASCII input only).
var calendarExportPattern = regexp.MustCompile(`^(\d{4})-(\d{1,2})-(\d{1,2})[T ](\d{1,2}):(\d{1,2})(?::(\d{1,2})(?:[.,](\d{1,6})\d*)?)?[ \t\n\r\f\v]*(Z|[+-]\d{2}(?::?\d{2})?)?\n?$`)

func calendarExportRegex(text string) (*calendarExportMoment, error) {
	match := calendarExportPattern.FindStringSubmatch(text)
	if match == nil {
		return nil, nil
	}
	number := func(index int) int {
		value, _ := strconv.Atoi(match[index])
		return value
	}
	second, micro := 0, 0
	if match[6] != "" {
		second = number(6)
	}
	if match[7] != "" {
		micro, _ = strconv.Atoi(match[7] + strings.Repeat("0", 6-len(match[7])))
	}
	utc, offset := false, 0
	switch zone := match[8]; {
	case zone == "":
	case zone == "Z":
		utc = true
	case strings.Trim(zone[1:], "0:") == "":
		utc = true
	default:
		// get_fixed_timezone: minutes past 59 add up (the regex takes any two digits).
		digits := strings.ReplaceAll(zone[1:], ":", "")
		offset, _ = strconv.Atoi(digits[:2])
		offset *= 60
		if len(digits) == 4 {
			minutes, _ := strconv.Atoi(digits[2:])
			offset += minutes
		}
		if zone[0] == '-' {
			offset = -offset
		}
	}
	moment, err := calendarExportBuild(number(1), number(2), number(3), number(4), number(5), second, micro, utc)
	if err != nil || offset == 0 {
		return moment, err
	}
	// The timezone is named "+HHMM" here; only the offsets of icalendar's table are ported.
	return calendarExportZoned(moment, offset, false)
}
