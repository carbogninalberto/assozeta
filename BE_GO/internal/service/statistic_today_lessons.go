package service

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// TodayLessons is statistic_dashboard's todaylessons widget after the
// association lookup: the days of the association's courses dated within
// [from, to] (the view's naive Rome-padded day), ordered by date, each
// rendered by get_extended_prop_for_attendance_day. When the acting person
// (acting_user, read without calling is_collaborator, so any person) is an
// instructor's associated user, only days of registries whose events mention
// that instructor are listed. Rendering a day can link an unlinked day to its
// event (AttendanceDay.save() with its auditlog entry); like the view, the
// request is not atomic.
func (s *CourseAttendance) TodayLessons(ctx context.Context, identity domain.ResolvedIdentity, audit domain.LoginAuditContext,
	association string, from, to time.Time) (any, error) {
	return s.run(ctx, identity, audit, func(r *attendanceRun) (any, error) {
		instructor, err := r.tx.InstructorOfUser(ctx, identity.Effective.ID)
		if err != nil {
			return nil, err
		}
		days, err := r.tx.TodayDays(ctx, association, from, to, instructor, r.scope.Association)
		if err != nil {
			return nil, err
		}
		lessons := []any{}
		for _, day := range days {
			r.labels = day.CourseAssociation
			attendees, err := calendarParse(day.Attendees)
			if err != nil {
				return nil, err
			}
			absences, err := calendarParse(day.ExpectedAbsences)
			if err != nil {
				return nil, err
			}
			registry := domain.AttendanceRegistryRow{ID: day.RegistryID, CourseID: day.CourseID, Events: day.Events}
			lesson, err := r.extendedProps(ctx, registry, day.AttendanceDayRow, attendees, &absences)
			if err != nil {
				return nil, err
			}
			lessons = append(lessons, lesson)
		}
		return calendarObject("data", calendarObject("lessons", lessons)), nil
	})
}

// CalendarLesson is get_extended_prop_for_attendance_day for the day
// full_events_calendar matched (get_lesson), inside the calendar view's own
// transaction: the day is read again (its registry is the one the view walks,
// unchanged by the request), and linking an unlinked day to its event is
// saved with its auditlog entry.
func (s *CourseAttendance) CalendarLesson(ctx context.Context, tx domain.CourseAttendanceTransaction, identity domain.ResolvedIdentity,
	audit domain.LoginAuditContext, registry domain.AttendanceRegistryRow, day string, scope *string) (any, error) {
	r := &attendanceRun{s: s, tx: tx, identity: identity, audit: audit, scope: domain.AttendanceScope{Association: scope}}
	row, err := tx.Day(ctx, day, nil, nil, scope)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, errCalendarRaised
	}
	r.labels = row.CourseAssociation
	attendees, err := calendarParse(row.Attendees)
	if err != nil {
		return nil, err
	}
	absences, err := calendarParse(row.ExpectedAbsences)
	if err != nil {
		return nil, err
	}
	return r.extendedProps(ctx, registry, *row, attendees, &absences)
}

// StatisticTodayLessons is the todaylessons widget (CourseAttendance).
type StatisticTodayLessons interface {
	TodayLessons(ctx context.Context, identity domain.ResolvedIdentity, audit domain.LoginAuditContext, association string,
		from, to time.Time) (any, error)
}

// todayLessonsError maps the attendance helpers' outcomes to the dashboard's:
// unhandled exceptions and refused inputs are both the 500 page.
func todayLessonsError(err error) error {
	switch {
	case errors.Is(err, domain.ErrCalendarNotPorted):
		return domain.ErrStatisticNotPorted
	case errors.Is(err, domain.ErrCalendarRaised):
		return ErrStatisticPythonError
	}
	return err
}
