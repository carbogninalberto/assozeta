package service

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// InstructorLessonsAnswer is one of instructor_lessons_hours's own error responses.
type InstructorLessonsAnswer struct {
	Status  int
	Message string
}

func (e *InstructorLessonsAnswer) Error() string { return "instructor lessons answer" }

// InstructorLessonsRequest carries the query parameters (last values).
type InstructorLessonsRequest struct {
	UID                *string
	StartDate, EndDate string
	IncludeLessons     bool
}

// lessonsDay is strptime(text, '%d/%m/%Y') (+ days) at Europe/Rome midnight,
// in UTC; ok=false is the ValueError or OverflowError.
func lessonsDay(text string, days int, rome *time.Location) (time.Time, bool) {
	day, ok := dto.ParseSubscriptionMembershipDate(text)
	if !ok {
		return time.Time{}, false
	}
	parsed, err := time.Parse("2006-01-02", day)
	if err != nil {
		return time.Time{}, false
	}
	if days > 0 && parsed.Year() == 9999 && parsed.Month() == 12 && parsed.Day() == 31 {
		return time.Time{}, false
	}
	parsed = parsed.AddDate(0, 0, days)
	moment := time.Date(parsed.Year(), parsed.Month(), parsed.Day(), 0, 0, 0, 0, rome).UTC()
	if moment.Year() < 1 || moment.Year() > 9999 {
		return time.Time{}, false
	}
	return moment, true
}

func lessonsRound(seconds float64) dto.PythonFloat {
	value, _ := strconv.ParseFloat(strconv.FormatFloat(seconds/3600, 'f', 2, 64), 64)
	return dto.PythonFloat(value)
}

func lessonsISO(value time.Time) string {
	text := value.UTC().Format("2006-01-02T15:04:05")
	if micros := value.Nanosecond() / 1000; micros != 0 {
		text += fmt.Sprintf(".%06d", micros)
	}
	return text + "+00:00"
}

// lessonsAssigned is extendedProps.instructor naming the instructor.
func lessonsAssigned(event domain.RequestObject, id string) bool {
	props, ok := lookupObject(event, "extendedProps")
	if !ok {
		return false
	}
	value, _ := props.Lookup("instructor")
	var instructors []any
	switch v := value.(type) {
	case domain.RequestObject:
		instructors = []any{v}
	case []any:
		instructors = v
	default:
		return false
	}
	for _, item := range instructors {
		if object, ok := item.(domain.RequestObject); ok {
			instructor, _ := object.Lookup("instructor_id")
			if dto.PythonStr(instructor) == id {
				return true
			}
		}
	}
	return false
}

func lookupObject(object domain.RequestObject, name string) (domain.RequestObject, bool) {
	value, _ := object.Lookup(name)
	result, ok := value.(domain.RequestObject)
	return result, ok
}

type lessonsCourse struct {
	id, title string
	count     int
	seconds   float64
	lessons   []map[string]any
	keys      [][2]string
}

// lessonsHours is get_instructor_lessons_hours.
func lessonsHours(instructor string, registries []domain.InstructorLessonRegistry, low, high *time.Time, include bool) (map[string]any, error) {
	total, count := 0.0, 0
	order := []*lessonsCourse{}
	courses := map[string]*lessonsCourse{}
	for _, registry := range registries {
		seconds, lessonsCount := 0.0, 0
		details, keys := []map[string]any{}, [][2]string{}
		var events []any
		if registry.Events != nil {
			parsed, err := dto.ParseProfileTablesJSON([]byte(*registry.Events))
			if err != nil {
				return nil, err
			}
			events, _ = parsed.([]any)
		}
		for _, item := range events {
			event, ok := item.(domain.RequestObject)
			if !ok {
				continue
			}
			if allDay, _ := event.Lookup("allDay"); dto.PythonTruthy(allDay) {
				continue
			}
			// The assignment has no side effect: checking it first skips
			// the same events.
			if !lessonsAssigned(event, instructor) {
				continue
			}
			rawStart, present := event.Lookup("start")
			if !present {
				continue
			}
			start, ok := dto.CalendarEventDateTime(rawStart)
			if !ok {
				continue
			}
			rawEnd, present := event.Lookup("end")
			if !present {
				continue
			}
			end, ok := dto.CalendarEventDateTime(rawEnd)
			if !ok {
				continue
			}
			if (low != nil && !end.After(*low)) || (high != nil && !start.Before(*high)) {
				continue
			}
			from, to := start, end
			if high != nil && high.Before(to) {
				to = *high
			}
			if low != nil && low.After(from) {
				from = *low
			}
			duration := float64(to.Sub(from).Microseconds()) / 1e6
			if duration <= 0 {
				continue
			}
			seconds += duration
			lessonsCount++
			if include {
				if start.Year() < 1 || start.Year() > 9999 || end.Year() < 1 || end.Year() > 9999 {
					// astimezone(utc) overflows.
					return nil, errInstructorWritesServer
				}
				eventID, _ := event.Lookup("event_id")
				if !dto.PythonTruthy(eventID) {
					eventID, _ = event.Lookup("id")
				}
				text := ""
				if dto.PythonTruthy(eventID) {
					text = dto.PythonStr(eventID)
				}
				var title any = "Lezione"
				if value, _ := event.Lookup("title"); dto.PythonTruthy(value) {
					title = json.RawMessage(dto.PythonJSONDumps(value))
				}
				details = append(details, map[string]any{"event_id": text, "title": title, "start": lessonsISO(start), "end": lessonsISO(end),
					"hours": lessonsRound(duration)})
				keys = append(keys, [2]string{lessonsISO(start), text})
			}
		}
		if lessonsCount == 0 {
			continue
		}
		entry, found := courses[registry.CourseID]
		if !found {
			entry = &lessonsCourse{id: registry.CourseID, title: registry.CourseTitle}
			courses[registry.CourseID] = entry
			order = append(order, entry)
		}
		entry.count += lessonsCount
		entry.seconds += seconds
		entry.lessons = append(entry.lessons, details...)
		entry.keys = append(entry.keys, keys...)
		total += seconds
		count += lessonsCount
	}
	sort.SliceStable(order, func(i, j int) bool { return order[i].title < order[j].title })
	items := make([]map[string]any, 0, len(order))
	for _, entry := range order {
		item := map[string]any{"course_id": entry.id, "course_title": entry.title, "lessons_count": entry.count, "hours": lessonsRound(entry.seconds)}
		if include {
			indexes := make([]int, len(entry.lessons))
			for i := range indexes {
				indexes[i] = i
			}
			sort.SliceStable(indexes, func(a, b int) bool {
				x, y := entry.keys[indexes[a]], entry.keys[indexes[b]]
				return x[0] < y[0] || x[0] == y[0] && x[1] < y[1]
			})
			lessons := make([]map[string]any, len(indexes))
			for i, index := range indexes {
				lessons[i] = entry.lessons[index]
			}
			item["lessons"] = lessons
		}
		items = append(items, item)
	}
	if math.IsNaN(total) || math.IsInf(total, 0) {
		return nil, errInstructorWritesServer
	}
	return map[string]any{"total_hours": lessonsRound(total), "lessons_count": count, "courses": items}, nil
}

// Lessons follows instructor_lessons_hours.
func (s *InstructorWrites) Lessons(ctx context.Context, identity domain.ResolvedIdentity, request InstructorLessonsRequest) (map[string]any, error) {
	user := identity.ScopeUser
	associations, err := s.store.Associations(ctx, user.ID)
	if err != nil {
		return nil, err
	}
	if len(associations) > 1 {
		// SportAssociation.objects.get: MultipleObjectsReturned.
		return nil, errInstructorWritesServer
	}
	var association *string
	if len(associations) == 1 {
		association = &associations[0]
	}
	acting := identity.Effective
	if acting == nil {
		acting = identity.Actor
	}
	var owner *string
	if association != nil {
		owner = &user.ID
	}
	requesting, err := s.store.RequestingInstructor(ctx, acting.ID, owner)
	if err != nil {
		return nil, err
	}
	rome, err := time.LoadLocation("Europe/Rome")
	if err != nil {
		return nil, err
	}
	var low, high *time.Time
	if request.StartDate != "" {
		value, ok := lessonsDay(request.StartDate, 0, rome)
		if !ok {
			return nil, &InstructorLessonsAnswer{Status: 400, Message: "Invalid start_date"}
		}
		low = &value
	}
	if request.EndDate != "" {
		value, ok := lessonsDay(request.EndDate, 1, rome)
		if !ok {
			return nil, &InstructorLessonsAnswer{Status: 400, Message: "Invalid end_date"}
		}
		high = &value
	}
	if low != nil && high != nil && !low.Before(*high) {
		return nil, &InstructorLessonsAnswer{Status: 400, Message: "Invalid date range"}
	}
	readAll := (user.Role == domain.RoleAssociation || user.Role == domain.RoleCollaborator) && association != nil && requesting == nil
	registries := map[string][]domain.InstructorLessonRegistry{}
	serialize := func(row domain.InstructorLessonRow, associationID string) (map[string]any, error) {
		if _, cached := registries[associationID]; !cached {
			rows, err := s.store.PublishedRegistries(ctx, associationID)
			if err != nil {
				return nil, err
			}
			registries[associationID] = rows
		}
		item, err := lessonsHours(row.ID, registries[associationID], low, high, request.UID != nil && request.IncludeLessons)
		if err != nil {
			return nil, err
		}
		item["instructor_id"], item["first_name"], item["last_name"] = row.ID, row.FirstName, row.LastName
		return item, nil
	}
	notAllowed := &InstructorLessonsAnswer{Status: 403, Message: "not allowed"}
	if request.UID != nil {
		id, err := messageReadsParseUUID(*request.UID)
		if err != nil {
			return nil, err
		}
		instructor, err := s.store.LessonInstructor(ctx, id)
		if err != nil {
			return nil, err
		}
		if instructor == nil {
			return nil, &InstructorLessonsAnswer{Status: 404, Message: "instructor not found"}
		}
		owned, err := s.store.FirstAssociation(ctx, instructor.UserID)
		if err != nil {
			return nil, err
		}
		if readAll {
			if owned == nil || *owned != *association {
				return nil, notAllowed
			}
		} else if requesting == nil || requesting.ID != *request.UID || owned == nil {
			// str(requesting_instructor.instructor_id) != str(uid): the raw path value.
			return nil, notAllowed
		}
		item, err := serialize(*instructor, *owned)
		if err != nil {
			return nil, err
		}
		return map[string]any{"data": item}, nil
	}
	if readAll {
		rows, err := s.store.AssociationInstructors(ctx, *association)
		if err != nil {
			return nil, err
		}
		items := make([]map[string]any, 0, len(rows))
		for _, row := range rows {
			item, err := serialize(row, *association)
			if err != nil {
				return nil, err
			}
			items = append(items, item)
		}
		return map[string]any{"data": items}, nil
	}
	if requesting == nil {
		return nil, notAllowed
	}
	owned, err := s.store.FirstAssociation(ctx, requesting.UserID)
	if err != nil {
		return nil, err
	}
	if owned == nil {
		return nil, notAllowed
	}
	item, err := serialize(*requesting, *owned)
	if err != nil {
		return nil, err
	}
	return map[string]any{"data": item}, nil
}
