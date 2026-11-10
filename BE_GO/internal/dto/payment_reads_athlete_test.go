package dto

import (
	"testing"
	"time"
)

func TestPaymentAthleteOneSolution(t *testing.T) {
	due := time.Date(2024, 9, 15, 0, 0, 0, 0, time.UTC)
	text := func(value string) *string { return &value }
	for _, test := range []struct {
		events    *string
		due       time.Time
		found, ok bool
	}{
		{nil, due, false, false},
		{text(`[]`), due, false, true},
		{text(`{}`), due, false, true},
		{text(`{"a": 1}`), due, false, false},
		{text(`""`), due, false, true},
		{text(`"x"`), due, false, false},
		{text(`3`), due, false, false},
		{text(`[{"id": 1, "payment_date": "15/09/2024"}, {"id": "0", "payment_date": "15/09/2024"}]`), due, true, true},
		{text(`[{"id": 0.0, "payment_date": "15/09/2024"}]`), due, true, true},
		{text(`[{"id": false, "payment_date": "15/09/2024"}]`), due, true, true},
		{text(`[{"id": 0, "payment_date": "16/09/2024"}]`), due, false, true},
		{text(`[{"id": 0, "payment_date": 5}, {"id": 2}]`), due, false, true},
		{text(`[{"id": 0}]`), due, false, false},
		{text(`[{"id": 1}]`), due, false, true},
		{text(`[{"id": "0.0", "payment_date": "15/09/2024"}]`), due, false, false},
		{text(`[{"id": null}]`), due, false, false},
		{text(`[{"payment_date": "15/09/2024"}]`), due, false, false},
		{text(`["0"]`), due, false, false},
		// str(datetime) with microseconds does not match the view's strptime format.
		{text(`[{"id": 0, "payment_date": "15/09/2024"}]`), due.Add(time.Microsecond), false, false},
		{text(`[]`), due.Add(time.Microsecond), false, true},
	} {
		found, ok := PaymentAthleteOneSolution(test.events, test.due)
		if found != test.found || ok != test.ok {
			name := "nil"
			if test.events != nil {
				name = *test.events
			}
			t.Errorf("%s: got %v %v, want %v %v", name, found, ok, test.found, test.ok)
		}
	}
}
