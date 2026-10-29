package service

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type AttendanceDays struct {
	store domain.AttendanceDayStore
	now   func() time.Time
}

func NewAttendanceDays(store domain.AttendanceDayStore, now func() time.Time) *AttendanceDays {
	return &AttendanceDays{store: store, now: now}
}

// Delete follows attendance_day_delete: is_valid_uuid (DRF 400), the
// course-owner lookup (a miss is the 404 for athletes too), then delete().
func (s *AttendanceDays) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return err
	}
	tx, err := s.store.BeginAttendanceDay(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = tx.OwnedAttendanceDay(ctx, id, identity.ScopeUser.ID, paymentCategoryAssociationScope(identity)); err != nil {
		return err
	}
	actorAssociation, err := tx.ActiveAssociationOf(ctx, identity.Actor.ID)
	if err != nil {
		return err
	}
	if err = tx.DeleteAttendanceDay(ctx, id, actorAssociation, paymentCategoryAuditContext(identity, audit),
		s.now().UTC().Truncate(time.Microsecond)); err != nil {
		return err
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	return tx.Commit()
}

// ErrAttendanceSubscriptionRequired is the bare-set 400 for a missing course_subscription_id.
var ErrAttendanceSubscriptionRequired = errors.New("error: course_subscription_id is required.")

// ErrAttendanceSubscriptionDenied is the view's PermissionDenied.
var ErrAttendanceSubscriptionDenied = errors.New("Iscrizione al corso non disponibile.")

// AttendanceAlreadyListed is the 200 set-literal answer returned before any save.
type AttendanceAlreadyListed struct{ List string }

func (e *AttendanceAlreadyListed) Error() string {
	return "error: course_subscription_id already in " + e.List + "."
}

type AttendanceMarks struct {
	store domain.AttendanceMarkStore
	now   func() time.Time
}

func NewAttendanceMarks(store domain.AttendanceMarkStore, now func() time.Time) *AttendanceMarks {
	return &AttendanceMarks{store: store, now: now}
}

// pythonUUIDValidation is is_valid_uuid's DRF ValidationError detail for a value.
func pythonUUIDValidation(value any) error {
	if text, ok := value.(string); ok {
		_, err := messageReadsParseUUID(text)
		return err
	}
	name := "dict"
	switch v := value.(type) {
	case bool:
		name = "bool"
	case json.Number:
		name = "int"
		if strings.ContainsAny(string(v), ".eE") {
			name = "float"
		}
	case []any:
		name = "list"
	}
	return &dto.MessageReadUUIDError{Detail: "'" + name + "' object has no attribute 'replace'"}
}

// Mark follows attendance_day_mark_absent.
func (s *AttendanceMarks) Mark(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, audit domain.LoginAuditContext) (string, error) {
	if err := ctx.Err(); err != nil {
		return "", err
	}
	day, err := messageReadsParseUUID(raw)
	if err != nil {
		return "", err
	}
	tx, err := s.store.BeginAttendanceMark(ctx)
	if err != nil {
		return "", err
	}
	defer tx.Rollback()
	scope := paymentCategoryAssociationScope(identity)
	var association, athlete *string
	if identity.ScopeUser.Role == domain.RoleAthlete {
		athlete = &identity.ScopeUser.ID
		association = scope
		if association == nil {
			if association, err = tx.PrimaryAssociation(ctx); err != nil {
				return "", err
			}
		}
		if association == nil {
			return "", domain.ErrAttendanceDayNotFound
		}
	}
	lists, err := tx.DayForMark(ctx, day, association, identity.ScopeUser.ID, scope)
	if err != nil {
		return "", err
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		return "", errAttendanceRaised
	}
	value, _ := object.Lookup("course_subscription_id")
	if value == nil {
		return "", ErrAttendanceSubscriptionRequired
	}
	if err = pythonUUIDValidation(value); err != nil {
		return "", err
	}
	subscription := value.(string)
	parsed, _ := paymentInfoParseUUID(subscription)
	visible, err := tx.CourseSubscriptionVisible(ctx, parsed.String(), lists.CourseID, lists.CourseAssociation, scope, athlete)
	if err != nil {
		return "", err
	}
	if !visible {
		return "", ErrAttendanceSubscriptionDenied
	}
	absentValue, present := object.Lookup("absent")
	absent := !present || dto.PythonTruthy(absentValue)
	attendees, err := attendanceList(lists.Attendees)
	if err != nil {
		return "", err
	}
	absences, err := attendanceList(lists.ExpectedAbsences)
	if err != nil {
		return "", err
	}
	oldAttendees, oldAbsences := attendanceRender(attendees), attendanceRender(absences)
	from, to, message := &attendees, &absences, "added to expected_absences and removed from attendees."
	list := "expected_absences"
	if !absent {
		from, to, message, list = &absences, &attendees, "removed from expected_absences and added to attendees.", "attendees"
	}
	if contains, err := attendanceContains(*from, subscription); err != nil {
		return "", err
	} else if contains {
		kept := []any{}
		for _, item := range (*from).([]any) {
			if id, _ := attendanceID(item); id != subscription {
				kept = append(kept, item)
			}
		}
		*from = kept
	}
	if contains, err := attendanceContains(*to, subscription); err != nil {
		return "", err
	} else if contains {
		return "", &AttendanceAlreadyListed{List: list}
	}
	items, _ := (*to).([]any)
	*to = append(items, domain.RequestObject{{Name: "course_subscription_id", Value: subscription}})
	changes := map[string][2]string{}
	if updated := attendanceRender(attendees); updated != oldAttendees {
		changes["attendees"] = [2]string{oldAttendees, updated}
	}
	if updated := attendanceRender(absences); updated != oldAbsences {
		changes["expected_absences"] = [2]string{oldAbsences, updated}
	}
	if len(changes) > 0 {
		if err = tx.AppendAttendanceAudit(ctx, domain.AttendanceListsAudit{DayID: day, AssociationID: lists.CourseAssociation,
			Changes: changes, Context: paymentCategoryAuditContext(identity, audit), At: s.now().UTC().Truncate(time.Microsecond)}); err != nil {
			return "", err
		}
	}
	if err = tx.SetAttendanceLists(ctx, day, attendanceStore(attendees), attendanceStore(absences)); err != nil {
		return "", err
	}
	if err = ctx.Err(); err != nil {
		return "", err
	}
	return message, tx.Commit()
}

var errAttendanceRaised = errors.New("attendance mark raised")

func attendanceList(text *string) (any, error) {
	if text == nil {
		return nil, nil
	}
	return dto.ParseProfileTablesJSON([]byte(*text))
}

func attendanceRender(value any) string { return string(dto.PythonJSONDumpsSorted(value)) }

func attendanceStore(value any) *string {
	if value == nil {
		return nil
	}
	text := string(dto.PythonJSONDumps(value))
	return &text
}

// attendanceID is attendee['course_subscription_id']: non-dict items and a
// missing key raise.
func attendanceID(item any) (string, error) {
	value, err := dto.PythonIndex(item, "course_subscription_id")
	if err != nil {
		return "", errAttendanceRaised
	}
	text, _ := value.(string)
	return text, nil
}

// attendanceContains is `list and any(str(id) == attendee['course_subscription_id'] ...)`.
func attendanceContains(list any, id string) (bool, error) {
	items, ok := list.([]any)
	if list == nil || (ok && len(items) == 0) {
		return false, nil
	}
	if !ok {
		return false, errAttendanceRaised
	}
	for _, item := range items {
		value, err := attendanceID(item)
		if err != nil {
			return false, err
		}
		if value == id {
			return true, nil
		}
	}
	return false, nil
}
