package service

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/xlsxexport"
	"github.com/google/uuid"
)

// InstructorReport ports instructor_report (instructor_views.py) and
// get_report (instructors_utils.py).
type InstructorReport struct {
	store domain.InstructorReportStore
	now   func() time.Time
}

func NewInstructorReport(store domain.InstructorReportStore, now func() time.Time) *InstructorReport {
	return &InstructorReport{store: store, now: now}
}

// ErrInstructorReportServer is an exception the view does not handle (the 500 page).
var ErrInstructorReportServer = errors.New("instructor report unhandled error")

// ErrInstructorReportNotPorted refuses input whose Django result is not reproduced (the 500 page).
var ErrInstructorReportNotPorted = refusal.New("instructor report input not ported")

// InstructorReportMissingDates is the view's 400 {'msg': 'Entrambe le date sono richieste.'}.
var InstructorReportMissingDates = errors.New("Entrambe le date sono richieste.")

var instructorReportSchema = []struct{ key, label string }{{"first_name", "Nome"}, {"last_name", "Cognome"}, {"course", "Corso"},
	{"title", "Titolo"}, {"description", "Descrizione"}, {"date", "Data"}, {"attendees_count", "Numero di partecipanti"},
	{"attendees", "Partecipanti"}, {"expected_absences", "Assenti previsti"}}

var instructorReportDate = regexp.MustCompile(`^(\d{1,2})([/.\-])(\d{1,2})([/.\-])(\d{4})$`)

// instructorReportParse is dateutil's parser.parse(text, dayfirst=True) for
// D/M/YYYY (separators '/', '-' or '.', surrounding whitespace): a day-first
// date that does not exist raises (500). Other spellings are not ported.
func instructorReportParse(text string) (time.Time, error) {
	match := instructorReportDate.FindStringSubmatch(strings.Trim(text, " \t\n\r\v\f"))
	if match == nil || match[2] != match[4] {
		return time.Time{}, ErrInstructorReportNotPorted
	}
	day, _ := strconv.Atoi(match[1])
	month, _ := strconv.Atoi(match[3])
	year, _ := strconv.Atoi(match[5])
	if month > 12 || day > 31 || day == 0 || month == 0 || year == 0 {
		return time.Time{}, ErrInstructorReportNotPorted // dateutil may swap or reject: not ported
	}
	value := time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC)
	if value.Day() != day {
		return time.Time{}, ErrInstructorReportServer // ParserError: day is out of range for month
	}
	return value, nil
}

type instructorReportRow struct {
	first, last string
	course      *string
	title       *string
	description any
	date        time.Time
	count       int64
	attendees   []any
	absences    []any
}

// Report follows instructor_report: request.user.sport_association, the
// two required dates (00:00:00 and 23:59:59 in Europe/Rome), the rows of
// every instructor of the user found in a day's calendar event, sorted by
// first name, last name and date, and the xlsxwriter report.
func (s *InstructorReport) Report(ctx context.Context, identity domain.ResolvedIdentity, start, end *string) (any, error) {
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return nil, &domain.PermissionDenied{Detail: "User is not a sport association"}
	}
	association, err := s.store.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	if start == nil || end == nil {
		return nil, InstructorReportMissingDates
	}
	from, err := instructorReportParse(*start)
	if err != nil {
		return nil, err
	}
	to, err := instructorReportParse(*end)
	if err != nil {
		return nil, err
	}
	rome, err := time.LoadLocation("Europe/Rome")
	if err != nil {
		return nil, err
	}
	from = time.Date(from.Year(), from.Month(), from.Day(), 0, 0, 0, 0, rome).UTC()
	to = time.Date(to.Year(), to.Month(), to.Day(), 23, 59, 59, 0, rome).UTC()
	instructors, err := s.store.Instructors(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	byID := map[string]domain.InstructorReportInstructor{}
	for _, instructor := range instructors {
		byID[instructor.ID] = instructor
	}
	registries, err := s.store.Registries(ctx, association)
	if err != nil {
		return nil, err
	}
	rows := []instructorReportRow{}
	ids := map[string]bool{}
	for _, registry := range registries {
		days, err := s.store.Days(ctx, registry.ID, from, to)
		if err != nil {
			return nil, err
		}
		if len(days) == 0 {
			continue
		}
		events, err := dto.AuditStoredJSON(registry.Events)
		if err != nil {
			return nil, ErrInstructorReportNotPorted
		}
		for _, day := range days {
			event, err := instructorReportEvent(events, day.AssociatedEvent)
			if err != nil {
				return nil, err
			}
			if event == nil {
				continue
			}
			if _, ok := event.Lookup("title"); !ok {
				return nil, ErrInstructorReportServer // logger.info(f"... {event['title']}"): KeyError
			}
			found := instructorReportPath(event, "instructor")
			description := instructorReportPath(event, "description")
			if found == nil {
				continue
			}
			var candidates []any
			switch typed := found.(type) {
			case []any:
				for _, item := range typed {
					object, ok := item.(domain.RequestObject)
					if !ok {
						return nil, ErrInstructorReportServer // inst.get: AttributeError
					}
					value, _ := object.Lookup("instructor_id")
					candidates = append(candidates, value)
				}
			case domain.RequestObject:
				// str(value): only a str can equal an instructor's id text.
				value, _ := typed.Lookup("instructor_id")
				candidates = []any{value}
			default:
				return nil, ErrInstructorReportServer // .get on a str, number or bool
			}
			for _, candidate := range candidates {
				id, ok := candidate.(string)
				instructor, known := byID[id]
				if !ok || !known {
					continue
				}
				row := instructorReportRow{first: instructor.FirstName, last: instructor.LastName, course: registry.Course, title: day.Title,
					description: description, date: day.Date}
				for _, list := range []struct {
					text *string
					into *[]any
				}{{day.Attendees, &row.attendees}, {day.ExpectedAbsences, &row.absences}} {
					items, err := instructorReportSubscriptions(list.text, ids)
					if err != nil {
						return nil, err
					}
					*list.into = items
				}
				count, err := instructorReportCount(day.Attendees)
				if err != nil {
					return nil, err
				}
				row.count = count
				rows = append(rows, row)
			}
		}
	}
	query := []string{}
	for id := range ids {
		query = append(query, id)
	}
	sort.Strings(query)
	names, err := s.store.Names(ctx, query)
	if err != nil {
		return nil, err
	}
	joined := func(items []any) string {
		parts := make([]string, len(items))
		for i, item := range items {
			key := "None"
			if text, ok := item.(string); ok {
				key = text
			}
			name, ok := names[key]
			if !ok {
				name = "Atleta cancellato"
			}
			parts[i] = name
		}
		return strings.Join(parts, ", ")
	}
	sort.SliceStable(rows, func(i, j int) bool {
		a, b := rows[i], rows[j]
		if a.first != b.first {
			return a.first < b.first
		}
		if a.last != b.last {
			return a.last < b.last
		}
		return a.date.Before(b.date)
	})
	results := make([]any, len(rows))
	frame := xlsxexport.Frame{Sheet: "Sheet1"}
	if len(rows) > 0 {
		for _, column := range instructorReportSchema {
			frame.Columns = append(frame.Columns, column.label)
		}
	}
	for i, row := range rows {
		description := row.description
		if !dto.PythonTruthy(description) {
			description = ""
		}
		stamp := row.date.In(rome).Format("02/01/2006 15:04")
		attendees, absences := joined(row.attendees), joined(row.absences)
		optional := func(value *string) any {
			if value == nil {
				return nil
			}
			return *value
		}
		results[i] = domain.RequestObject{{Name: "first_name", Value: row.first}, {Name: "last_name", Value: row.last},
			{Name: "course", Value: optional(row.course)}, {Name: "title", Value: optional(row.title)},
			{Name: "description", Value: description}, {Name: "date", Value: stamp}, {Name: "attendees_count", Value: json.Number(strconv.FormatInt(row.count, 10))},
			{Name: "attendees", Value: attendees}, {Name: "expected_absences", Value: absences}}
		cell, err := instructorReportCell(description)
		if err != nil {
			return nil, err
		}
		frame.Rows = append(frame.Rows, []xlsxexport.Value{xlsxexport.StrValue(row.first), xlsxexport.StrValue(row.last),
			xlsxexport.OptionalStr(row.course), xlsxexport.OptionalStr(row.title), cell, xlsxexport.StrValue(stamp),
			xlsxexport.IntValue(row.count), xlsxexport.StrValue(attendees), xlsxexport.StrValue(absences)})
	}
	for c, label := range frame.Columns {
		column := make([]xlsxexport.Value, len(frame.Rows))
		for r, row := range frame.Rows {
			column[r] = row[c]
		}
		longest, err := xlsxexport.StrLength(column)
		if err != nil {
			return nil, ErrInstructorReportNotPorted
		}
		frame.Widths = append(frame.Widths, float64(max(longest, len([]rune(label)))+2))
	}
	workbook, err := xlsxexport.PandasXlsxwriter(frame, s.now())
	var report any
	switch {
	case errors.Is(err, xlsxexport.ErrNotPorted):
		return nil, ErrInstructorReportNotPorted
	case err != nil:
		report = nil // generate_report_from_data logs and returns None
	default:
		report = base64.StdEncoding.EncodeToString(workbook)
	}
	return domain.RequestObject{{Name: "data", Value: domain.RequestObject{{Name: "results", Value: results},
		{Name: "report_file", Value: report}}}}, nil
}

// instructorReportEvent finds the day's event: e['event_id'] ==
// str(associated_event) over the events, in order, up to the first match.
func instructorReportEvent(events any, associated *string) (domain.RequestObject, error) {
	target := "None"
	if associated != nil {
		target = *associated
	}
	var items []any
	switch typed := events.(type) {
	case []any:
		items = typed
	case string:
		if typed == "" {
			return nil, nil
		}
		return nil, ErrInstructorReportServer // str indices must be integers
	case domain.RequestObject:
		if len(typed) == 0 {
			return nil, nil
		}
		return nil, ErrInstructorReportServer
	default:
		return nil, ErrInstructorReportServer // None or a number is not iterable
	}
	for _, item := range items {
		event, ok := item.(domain.RequestObject)
		if !ok {
			return nil, ErrInstructorReportServer
		}
		id, ok := event.Lookup("event_id")
		if !ok {
			return nil, ErrInstructorReportServer // KeyError
		}
		if text, isText := id.(string); isText && text == target {
			return event, nil
		}
	}
	return nil, nil
}

// instructorReportPath is get_value_by_path(event, 'extendedProps.<key>').
func instructorReportPath(event domain.RequestObject, key string) any {
	props, ok := event.Lookup("extendedProps")
	object, isObject := props.(domain.RequestObject)
	if !ok || !isObject {
		return nil
	}
	value, _ := object.Lookup(key)
	return value
}

// instructorReportSubscriptions is the loop over attendees (or expected
// absences): each item's 'course_subscription_id', also added to ids.
func instructorReportSubscriptions(text *string, ids map[string]bool) ([]any, error) {
	value, err := dto.AuditStoredJSON(text)
	if err != nil {
		return nil, ErrInstructorReportNotPorted
	}
	if value == nil {
		return []any{}, nil
	}
	var items []any
	switch typed := value.(type) {
	case []any:
		items = typed
	case domain.RequestObject:
		if len(typed) > 0 {
			return nil, ErrInstructorReportServer // the keys are str
		}
	case string:
		if typed != "" {
			return nil, ErrInstructorReportServer
		}
	default:
		return nil, ErrInstructorReportServer
	}
	out := []any{}
	for _, item := range items {
		object, ok := item.(domain.RequestObject)
		if !ok {
			return nil, ErrInstructorReportServer
		}
		id, ok := object.Lookup("course_subscription_id")
		if !ok {
			return nil, ErrInstructorReportServer // KeyError
		}
		switch typed := id.(type) {
		case nil:
			// None is dropped from the IN list and renders 'None'.
		case string:
			if _, err := instructorReportUUID(typed); err != nil {
				return nil, err
			}
			ids[typed] = true
		default:
			return nil, ErrInstructorReportNotPorted // UUIDField.to_python of a number, bool, list or object
		}
		out = append(out, id)
	}
	return out, nil
}

// instructorReportUUID is uuid.UUID(text) as UUIDField.to_python reads it:
// a malformed value is a ValidationError (500).
func instructorReportUUID(text string) (string, error) {
	hex := strings.ReplaceAll(strings.TrimPrefix(strings.Trim(text, "{}"), "urn:uuid:"), "-", "")
	if len(hex) != 32 {
		return "", ErrInstructorReportServer
	}
	parsed, err := uuid.Parse(hex)
	if err != nil {
		return "", ErrInstructorReportServer
	}
	return parsed.String(), nil
}

// instructorReportCount is len(attendees) if attendees else 0.
func instructorReportCount(text *string) (int64, error) {
	value, err := dto.AuditStoredJSON(text)
	if err != nil {
		return 0, ErrInstructorReportNotPorted
	}
	switch typed := value.(type) {
	case []any:
		return int64(len(typed)), nil
	case domain.RequestObject:
		return int64(len(typed)), nil
	case string:
		return int64(len([]rune(typed))), nil
	case nil:
		return 0, nil
	}
	return 0, ErrInstructorReportServer
}

// instructorReportCell is the description as the DataFrame holds it.
func instructorReportCell(value any) (xlsxexport.Value, error) {
	switch typed := value.(type) {
	case string:
		return xlsxexport.StrValue(typed), nil
	case bool:
		return xlsxexport.BoolValue(typed), nil
	case json.Number:
		text := typed.String()
		if !strings.ContainsAny(text, ".eE") {
			number, err := strconv.ParseInt(text, 10, 64)
			if err != nil {
				return xlsxexport.Value{}, ErrInstructorReportNotPorted
			}
			return xlsxexport.IntValue(number), nil
		}
		number, err := strconv.ParseFloat(text, 64)
		if err != nil && !errors.Is(err, strconv.ErrRange) {
			return xlsxexport.Value{}, ErrInstructorReportNotPorted
		}
		return xlsxexport.FloatValue(number), nil
	case []any, domain.RequestObject:
		return xlsxexport.ObjectValue(dto.PythonStr(typed)), nil
	}
	return xlsxexport.Value{}, ErrInstructorReportNotPorted
}
