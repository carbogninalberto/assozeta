package service

import (
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// internal/pydateutil reads every Python str (long tail PR 3), so a date text
// that is not valid UTF-8 is decoded like Django's form and query text and
// answered, never handed off. The three sites: the associate draft edit's
// born_date, the instructor report's start_date/end_date and the subscription
// import's date cells.
func TestDateutilSitesAnswerInvalidUTF8(t *testing.T) {
	now := time.Date(2024, time.March, 1, 12, 0, 0, 0, time.UTC)
	text := "\xff" // decoded as U+FFFD: dateutil raises ParserError
	check := func(t *testing.T, name string, err error) {
		t.Helper()
		if errors.Is(err, refusal.ErrNotPorted) {
			t.Fatalf("%s: a date text must not reach the refusal path, got %v", name, err)
		}
	}
	t.Run("associate draft born_date", func(t *testing.T) {
		drafts := NewAssociateDrafts(nil, func() time.Time { return now }, nil)
		_, err := drafts.associateDraftValidity(domain.RequestObject{{Name: "associate",
			Value: domain.RequestObject{{Name: "born_date", Value: text}}}})
		check(t, "associate draft", err)
	})
	t.Run("instructor report start_date", func(t *testing.T) {
		_, err := instructorReportParse(text, now)
		check(t, "instructor report", err)
		if !errors.Is(err, ErrInstructorReportServer) {
			t.Fatalf("instructor report: expected the 500 page (parse raises), got %v", err)
		}
	})
	t.Run("subscription import date cell", func(t *testing.T) {
		_, parsed, err := subscriptionImportDate(text, now)
		check(t, "subscription import", err)
		if parsed || err != nil {
			t.Fatalf("subscription import: expected import_date's None, got parsed=%v err=%v", parsed, err)
		}
	})
}
