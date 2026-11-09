package service

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"math/big"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
)

// AutoMarkAttendance is the auto_mark_attendance task body.
type AutoMarkAttendance struct {
	store  domain.AutoMarkAttendanceStore
	now    func() time.Time
	logger *slog.Logger
}

func NewAutoMarkAttendance(store domain.AutoMarkAttendanceStore, now func() time.Time, logger *slog.Logger) *AutoMarkAttendance {
	return &AutoMarkAttendance{store: store, now: now, logger: logger}
}

// errAutoMarkStopped is a Python exception the task catches: it prints the
// error and returns None, keeping every save committed before it.
var errAutoMarkStopped = errors.New("auto mark attendance stopped by stored data")

// Run follows the task: owners with auto_mark_attendance, their first live
// association, its active courses, the first registry, the days not yet
// marked within the last day. Each carnet save and each day save commits on
// its own, as autocommitted save() calls do; stored data that makes Python
// raise ends the run with the earlier saves kept and a NULL result.
func (s *AutoMarkAttendance) Run(ctx context.Context, job domain.Job) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	var payload map[string]json.RawMessage
	if job.AssociationID != uuid.Nil || job.ID == uuid.Nil || job.LeaseToken == uuid.Nil || job.State != "running" || job.Kind != domain.JobAutoMarkAttendance || job.Version != 1 || len(job.Payload) > 65536 || json.Unmarshal(job.Payload, &payload) != nil || payload == nil || len(payload) != 0 {
		return domain.ErrJobInvalid
	}
	reader, err := s.store.BeginAutoMarkAttendance(ctx, job)
	if err != nil {
		return err
	}
	defer reader.Rollback()
	marked, err := s.mark(ctx, job, reader)
	if err != nil && !errors.Is(err, errAutoMarkStopped) {
		return err
	}
	if err != nil {
		s.logger.WarnContext(ctx, "auto mark attendance stopped", "code", "auto_mark_attendance_stopped")
	}
	if err = reader.Rollback(); err != nil {
		return err
	}
	completion, err := s.store.BeginAutoMarkAttendance(ctx, job)
	if err != nil {
		return err
	}
	defer completion.Rollback()
	if err = completion.CompleteJob(ctx, job.ID, job.LeaseToken); err != nil {
		return err
	}
	if err = completion.Commit(); err != nil {
		return err
	}
	s.logger.InfoContext(ctx, "auto mark attendance processed", "days", marked)
	return nil
}

func (s *AutoMarkAttendance) mark(ctx context.Context, job domain.Job, reader domain.AutoMarkAttendanceTransaction) (int, error) {
	owners, err := reader.Owners(ctx)
	if err != nil {
		return 0, err
	}
	marked := 0
	for _, owner := range owners {
		association, err := reader.FirstAssociation(ctx, owner)
		if err != nil {
			return marked, err
		}
		if association == nil {
			continue
		}
		courses, err := reader.ActiveCourses(ctx, *association)
		if err != nil {
			return marked, err
		}
		for _, course := range courses {
			count, err := s.course(ctx, job, reader, course)
			marked += count
			if err != nil {
				return marked, err
			}
		}
	}
	return marked, nil
}

func (s *AutoMarkAttendance) course(ctx context.Context, job domain.Job, reader domain.AutoMarkAttendanceTransaction, course domain.AutoMarkCourse) (int, error) {
	registry, err := reader.FirstRegistry(ctx, course.ID)
	if err != nil || registry == nil {
		return 0, err
	}
	// date__range=[datetime.now() - timedelta(days=1), datetime.now()], naive
	// UTC (TIME_ZONE), both bounds inclusive.
	now := s.now().UTC().Truncate(time.Microsecond)
	days, err := reader.RangeDays(ctx, *registry, now.Add(-24*time.Hour), s.now().UTC().Truncate(time.Microsecond))
	if err != nil {
		return 0, err
	}
	// The values_list queryset is evaluated once, on the first day it is used.
	var subscriptions []uuid.UUID
	loaded, marked := false, 0
	for _, day := range days {
		// attendance_date < today_date and attendance_date > today_date - 1 day:
		// aware comparisons, so Europe/Rome only changes the representation.
		today := s.now()
		if !day.Date.Before(today) || !day.Date.After(today.Add(-24*time.Hour)) {
			continue
		}
		if !loaded {
			if subscriptions, err = reader.CourseSubscriptionIDs(ctx, course.ID); err != nil {
				return marked, err
			}
			loaded = true
		}
		if err = s.day(ctx, job, reader, course, day, subscriptions); err != nil {
			return marked, err
		}
		marked++
	}
	return marked, nil
}

func (s *AutoMarkAttendance) day(ctx context.Context, job domain.Job, reader domain.AutoMarkAttendanceTransaction, course domain.AutoMarkCourse, day domain.AutoMarkDay, subscriptions []uuid.UUID) error {
	oldAttendees, err := autoMarkJSON(day.Attendees)
	if err != nil {
		return err
	}
	absences, err := autoMarkJSON(day.ExpectedAbsences)
	if err != nil {
		return err
	}
	oldAbsences := absences
	if absences == nil {
		absences = []any{}
	}
	attendees := []any{}
	for _, attendee := range subscriptions {
		subscription, err := reader.CourseSubscription(ctx, attendee)
		if err != nil {
			return err
		}
		expected, err := autoMarkExpectedAbsence(absences, attendee.String())
		if err != nil {
			return err
		}
		if expected || subscription == nil {
			continue
		}
		carnets, err := reader.Carnets(ctx, subscription.ID, subscription.SubscriptionID)
		if err != nil {
			return err
		}
		var chosen *domain.AutoMarkCarnet
		var chosenMeta domain.RequestObject
		var chosenLeft any
		for i := range carnets {
			meta, left, err := autoMarkLessonsLeft(carnets[i].Meta)
			if err != nil {
				return err
			}
			if autoMarkRational(left).Sign() > 0 && (chosen == nil || autoMarkRational(chosenLeft).Cmp(autoMarkRational(left)) > 0) {
				chosen, chosenMeta, chosenLeft = &carnets[i], meta, left
			}
		}
		entry := domain.RequestObject{{Name: "course_subscription_id", Value: attendee.String()}}
		switch {
		case chosen != nil:
			if chosen.PaymentID != nil {
				paid, err := reader.PaymentPaid(ctx, *chosen.PaymentID)
				if err != nil {
					return err
				}
				if !paid {
					continue
				}
			}
			if err = s.saveCarnet(ctx, job, *chosen, chosenMeta, chosenLeft, course, day); err != nil {
				return err
			}
			attendees = append(attendees, entry)
		case len(carnets) > 0:
		default:
			attendees = append(attendees, entry)
		}
	}
	changes := map[string][2]string{}
	if before, after := autoMarkRender(oldAttendees), autoMarkRender(attendees); before != after {
		changes["attendees"] = [2]string{before, after}
	}
	if before, after := autoMarkRender(oldAbsences), autoMarkRender(absences); before != after {
		changes["expected_absences"] = [2]string{before, after}
	}
	changes["auto_marked"] = [2]string{"False", "True"}
	writer, err := s.store.BeginAutoMarkAttendance(ctx, job)
	if err != nil {
		return err
	}
	defer writer.Rollback()
	if err = writer.SaveDay(ctx, day, course.AssociationID, string(dto.PythonJSONDumps(attendees)), string(dto.PythonJSONDumps(absences)),
		changes, s.now().UTC().Truncate(time.Microsecond)); err != nil {
		return err
	}
	return writer.Commit()
}

// saveCarnet applies meta['lessons_left'] -= 1 and appends the registry entry,
// then commits carnet.save() on its own.
func (s *AutoMarkAttendance) saveCarnet(ctx context.Context, job domain.Job, carnet domain.AutoMarkCarnet, meta domain.RequestObject, left any, course domain.AutoMarkCourse, day domain.AutoMarkDay) error {
	updated := make(domain.RequestObject, len(meta))
	copy(updated, meta)
	registryIndex := -1
	for i, field := range updated {
		switch field.Name {
		case "lessons_left":
			updated[i].Value = autoMarkDecrement(left)
		case "lessons_registry":
			registryIndex = i
		}
	}
	if registryIndex < 0 {
		return errAutoMarkStopped // KeyError
	}
	registry, ok := updated[registryIndex].Value.([]any)
	if !ok {
		return errAutoMarkStopped // AttributeError: no append
	}
	var courseTitle, dayTitle any
	if course.Title != nil {
		courseTitle = *course.Title
	}
	if day.Title != nil {
		dayTitle = *day.Title
	}
	registry = append(append([]any(nil), registry...), domain.RequestObject{
		{Name: "date", Value: pythonAwareUTC(day.Date)},
		{Name: "course", Value: domain.RequestObject{{Name: "id", Value: course.ID.String()}, {Name: "title", Value: courseTitle}}},
		{Name: "title", Value: dayTitle},
	})
	updated[registryIndex].Value = registry
	changes := map[string][2]string{}
	if before, after := autoMarkRender(meta), autoMarkRender(updated); before != after {
		changes["meta"] = [2]string{before, after}
	}
	writer, err := s.store.BeginAutoMarkAttendance(ctx, job)
	if err != nil {
		return err
	}
	defer writer.Rollback()
	if err = writer.SaveCarnetMeta(ctx, carnet, string(dto.PythonJSONDumps(updated)), changes, s.now().UTC().Truncate(time.Microsecond)); err != nil {
		return err
	}
	return writer.Commit()
}

func autoMarkJSON(text *string) (any, error) {
	if text == nil {
		return nil, nil
	}
	value, err := dto.ParseProfileTablesJSON([]byte(*text))
	if err != nil {
		return nil, err
	}
	return value, nil
}

// autoMarkRender is auditlog's JSONField value: json.dumps(sort_keys=True).
func autoMarkRender(value any) string { return string(dto.PythonJSONDumpsSorted(value)) }

// autoMarkExpectedAbsence evaluates `expected_absences and any(str(attendee) ==
// item['course_subscription_id'] for item in expected_absences)`.
func autoMarkExpectedAbsence(absences any, attendee string) (bool, error) {
	if !dto.PythonTruthy(absences) {
		return false, nil
	}
	items, ok := absences.([]any)
	if !ok {
		// A dict iterates its keys and a string its characters; neither can be
		// subscripted with a string. Numbers and booleans are not iterable.
		return false, errAutoMarkStopped
	}
	for _, item := range items {
		object, ok := item.(domain.RequestObject)
		if !ok {
			return false, errAutoMarkStopped
		}
		value, present := object.Lookup("course_subscription_id")
		if !present {
			return false, errAutoMarkStopped
		}
		if text, ok := value.(string); ok && text == attendee {
			return true, nil
		}
	}
	return false, nil
}

// autoMarkLessonsLeft reads c.meta['lessons_left'] for the `> 0` comparison:
// meta must be a dict with the key and the value an int, float or bool.
func autoMarkLessonsLeft(text *string) (domain.RequestObject, any, error) {
	value, err := autoMarkJSON(text)
	if err != nil {
		return nil, nil, err
	}
	meta, ok := value.(domain.RequestObject)
	if !ok {
		return nil, nil, errAutoMarkStopped
	}
	left, present := meta.Lookup("lessons_left")
	if !present {
		return nil, nil, errAutoMarkStopped
	}
	switch left.(type) {
	case json.Number, bool:
		return meta, left, nil
	}
	return nil, nil, errAutoMarkStopped
}

// autoMarkRational is the exact value Python compares (int, float or bool).
func autoMarkRational(value any) *big.Rat {
	switch v := value.(type) {
	case bool:
		if v {
			return big.NewRat(1, 1)
		}
		return new(big.Rat)
	case json.Number:
		if autoMarkIsFloat(v) {
			f, _ := strconv.ParseFloat(string(v), 64)
			return new(big.Rat).SetFloat64(f)
		}
		r, _ := new(big.Rat).SetString(string(v))
		return r
	}
	return new(big.Rat)
}

func autoMarkIsFloat(value json.Number) bool { return strings.ContainsAny(string(value), ".eE") }

// autoMarkDecrement is `-= 1`: a bool becomes an int, an int stays exact and a
// float is an IEEE double subtraction.
func autoMarkDecrement(value any) any {
	switch v := value.(type) {
	case bool:
		if v {
			return json.Number("0")
		}
		return json.Number("-1")
	case json.Number:
		if autoMarkIsFloat(v) {
			f, _ := strconv.ParseFloat(string(v), 64)
			return f - 1
		}
		n, _ := new(big.Int).SetString(string(v), 10)
		return json.Number(n.Sub(n, big.NewInt(1)).String())
	}
	return value
}
