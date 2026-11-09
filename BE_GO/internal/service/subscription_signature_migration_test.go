package service

import (
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// Outcomes of CPython 3.13 base64.b64decode(text) (validate=False).
func TestSubscriptionSignatureLenientBase64(t *testing.T) {
	cases := map[string]bool{"abc": false, "abcd": true, "a": false, "ab==": true, "a===": false, "!!!!": true, " ": true,
		"": true, "ab=c": false, "abc=d": true, "é": false, "ab\ncd": true, "ab=": false, "=abcd": true, "abc===": true,
		"iVBORw0KGgo=": true, "iVBORw0KGgo": false}
	for text, want := range cases {
		if got := subscriptionSignatureLenientBase64(text); got != want {
			t.Errorf("%q: %v, want %v", text, got, want)
		}
	}
	if got := subscriptionSignatureMigrationPrefix("éa", 1); got != "é" {
		t.Errorf("prefix %q", got)
	}
}

func TestSubscriptionSignatureMigrationSaveError(t *testing.T) {
	created := time.Date(2024, 3, 1, 10, 0, 0, 0, time.UTC)
	row := func(month, day int, edit func(*domain.SubscriptionSignatureMigrationRow)) domain.SubscriptionSignatureMigrationRow {
		r := domain.SubscriptionSignatureMigrationRow{CreationDate: created, StartMonth: month, StartDay: day, EndMonth: 12, EndDay: 31}
		if edit != nil {
			edit(&r)
		}
		return r
	}
	cases := []struct {
		row  domain.SubscriptionSignatureMigrationRow
		want string
	}{
		{row(1, 1, nil), ""},
		{row(0, 1, nil), "Month must be between 1 and 12"},
		{row(1, 0, nil), "Day must be between 1 and 31"},
		{row(4, 31, nil), "Invalid date combination: day is out of range for month"},
		{row(2, 29, nil), "day is out of range for month"}, // 2024 leap, 2023 not
		{row(2, 29, func(r *domain.SubscriptionSignatureMigrationRow) { r.CreationDate = created.AddDate(1, 0, 0) }),
			"Invalid date combination: day is out of range for month"},
		{row(9, 1, func(r *domain.SubscriptionSignatureMigrationRow) { r.CustomEnd, r.EndMonth, r.EndDay = true, 2, 30 }),
			"day is out of range for month"},
		{row(9, 1, func(r *domain.SubscriptionSignatureMigrationRow) { r.CustomEnd, r.EndMonth, r.EndDay = true, 0, 30 }), ""},
	}
	for index, c := range cases {
		if got := subscriptionSignatureMigrationSaveError(c.row); got != c.want {
			t.Errorf("case %d: %q, want %q", index, got, c.want)
		}
	}
}
