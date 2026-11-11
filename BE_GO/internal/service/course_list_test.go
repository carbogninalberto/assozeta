package service

import (
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestBalanceSheetRangeBounds(t *testing.T) {
	at := func(text string) time.Time {
		value, err := time.Parse(time.RFC3339, text)
		if err != nil {
			t.Fatal(err)
		}
		return value
	}
	for _, test := range []struct {
		name, now  string
		day, month int64
		from, to   string
		ok         bool
	}{
		{"default start", "2026-11-01T10:00:00Z", 1, 1, "2026-01-01T00:00:00Z", "2026-12-31T00:00:00Z", true},
		{"before the start", "2026-08-31T23:59:59Z", 1, 9, "2025-09-01T00:00:00Z", "2026-08-31T00:00:00Z", true},
		{"at the start", "2026-09-01T00:00:00Z", 1, 9, "2026-09-01T00:00:00Z", "2027-08-31T00:00:00Z", true},
		{"start on the 31st", "2026-03-30T00:00:00Z", 31, 3, "2025-03-31T00:00:00Z", "2026-03-30T00:00:00Z", true},
		{"29 February", "2028-03-01T00:00:00Z", 29, 2, "", "", false},
		{"31 April", "2026-05-01T00:00:00Z", 31, 4, "", "", false},
		{"month zero", "2026-05-01T00:00:00Z", 1, 0, "", "", false},
		{"day zero", "2026-05-01T00:00:00Z", 0, 1, "", "", false},
	} {
		from, to, ok := balanceSheetRange(at(test.now), test.day, test.month)
		if ok != test.ok || (ok && (!from.Equal(at(test.from)) || !to.Equal(at(test.to)))) {
			t.Errorf("%s: got %v %v %v", test.name, from, to, ok)
		}
	}
}

func TestCourseListOrdering(t *testing.T) {
	for _, test := range []struct {
		name, column     string
		descending, rand bool
		unported         bool
		invalid          bool
		server           bool
	}{
		{name: "title", column: "title"},
		{name: "-title", column: "title", descending: true},
		{name: "pk", column: "course_id"},
		{name: "-sport_association", column: "sport_association_id", descending: true},
		{name: "group_id", column: "group_id"},
		{name: "?", rand: true},
		{name: "title__lower", column: "title", invalid: true},
		{name: "pk__gt", column: "course_id", invalid: true},
		{name: "creation_date__date__year", column: "creation_date"},
		{name: "creation_date__year__date", column: "creation_date", invalid: true},
		{name: "-events__0__amount", column: "events", descending: true},
		{name: "events__99999999999", column: "events", invalid: true},
		{name: "group__name", unported: true},
		{name: "tags", unported: true},
		{name: "-coursesubscription__paid", unported: true},
		{name: "sport_association__denomination", unported: true},
		{name: "-?", server: true},
		{name: "--title", server: true},
		{name: "-", server: true},
		{name: "nope", server: true},
		{name: "athletes", server: true},
	} {
		query := domain.CourseListQuery{}
		unported, err := courseListOrdering(&query, test.name)
		if errors.Is(err, ErrCourseListServer) != test.server || unported != test.unported || query.Random != test.rand ||
			query.OrderColumn != test.column || query.Descending != test.descending || query.OrderInvalid != test.invalid {
			t.Errorf("%s: got %+v unported=%v err=%v", test.name, query, unported, err)
		}
	}
}

func TestCourseListAthletesStopAtMissingAssociate(t *testing.T) {
	text := func(value string) *string { return &value }
	created := time.Date(2026, 9, 2, 10, 0, 0, 500000000, time.UTC)
	rows := []domain.CourseListAthlete{
		{Paid: true, CreationDate: created},
		{CreationDate: created, SubscriptionID: text("s1"), AssociateID: text("a1"), FirstName: text("Mario"), Sex: text("M")},
		{CreationDate: created, SubscriptionID: text("s2")},
		{CreationDate: created, SubscriptionID: text("s3"), AssociateID: text("a3")},
	}
	athletes := courseListAthletes(rows)
	if len(athletes) != 3 {
		t.Fatalf("got %d entries, want 3 (the list stops after the missing associate)", len(athletes))
	}
	first := athletes["0"].(map[string]any)
	if _, ok := first["subscription"]; ok || first["creation_date"] != "2026-09-02T10:00:00.500000Z" || first["paid"] != true {
		t.Errorf("first entry: %v", first)
	}
	if _, ok := athletes["1"].(map[string]any)["subscription"]; !ok {
		t.Errorf("second entry lacks its subscription")
	}
	if _, ok := athletes["2"].(map[string]any)["subscription"]; ok {
		t.Errorf("an entry without an associate keeps only paid and creation_date")
	}
}
