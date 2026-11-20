package service

import (
	"context"
	"slices"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type calendarWithoutAttendanceDays struct {
	domain.CalendarTransaction
}

func (calendarWithoutAttendanceDays) DayMatch(context.Context, string, *string, *time.Time, *string) (*string, error) {
	return nil, nil
}

func TestSubstituteInstructorSeesOnlyAssignedLessons(t *testing.T) {
	friday, tuesday := "friday", "tuesday"
	regular, substitute := "regular", "substitute"
	stored := `[
		{"event_id":"friday","title":"Gym Class","start":"2030-01-04T18:00:00Z",
		 "extendedProps":{"instructor":{"instructor_id":"regular"}}},
		{"event_id":"tuesday","title":"Gym Class","start":"2030-01-08T18:00:00Z",
		 "extendedProps":{"instructor":[{"instructor_id":"regular"},{"instructor_id":"substitute"}]}},
		{"event_id":"unassigned","title":"Gym Class","start":"2030-01-11T18:00:00Z"}
	]`
	registry := domain.CalendarRegistry{ID: "registry", CourseID: "course", Events: &stored}
	calendar := calendarWrite{tx: calendarWithoutAttendanceDays{}}
	start := time.Date(2030, time.January, 1, 0, 0, 0, 0, time.UTC)
	end := start.AddDate(0, 1, 0)

	for _, tt := range []struct {
		name       string
		instructor *string
		eventID    *string
		want       []string
	}{
		{name: "substitute's month", instructor: &substitute, want: []string{tuesday}},
		{name: "regular instructor's month", instructor: &regular, want: []string{friday, tuesday}},
		{name: "shared lesson detail", instructor: &substitute, eventID: &tuesday, want: []string{tuesday}},
		{name: "regular lesson detail", instructor: &substitute, eventID: &friday},
	} {
		t.Run(tt.name, func(t *testing.T) {
			events, err := calendar.registryEvents(t.Context(), registry, tt.instructor, tt.eventID, &start, &end, nil, nil, false, nil)
			if err != nil {
				t.Fatal(err)
			}
			got := make([]string, 0, len(events))
			for _, event := range events {
				id, _ := event.(domain.RequestObject).Lookup("event_id")
				got = append(got, id.(string))
			}
			if !slices.Equal(got, tt.want) {
				t.Fatalf("event IDs = %v, want %v", got, tt.want)
			}
		})
	}
}
