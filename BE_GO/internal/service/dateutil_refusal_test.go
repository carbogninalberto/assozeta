package service

import (
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pydateutil"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// A text internal/pydateutil refuses is pydateutil's own refusal: the services
// hand that sentinel on unchanged, so the handoff still replays it as a
// refusal (refusal.ErrNotPorted) and no family sentinel is invented for it.
// The three sites: the associate draft edit's born_date, the instructor
// report's start_date/end_date and the subscription import's date cells.
func TestAssociateDraftDateutilRefusalSentinels(t *testing.T) {
	now := time.Date(2024, time.March, 1, 12, 0, 0, 0, time.UTC)
	refused := "\xff" // not valid UTF-8, the one text pydateutil refuses here
	check := func(t *testing.T, name string, err error) {
		t.Helper()
		if !errors.Is(err, pydateutil.ErrUnsupported) {
			t.Fatalf("%s: expected pydateutil.ErrUnsupported, got %v", name, err)
		}
		if !errors.Is(err, refusal.ErrNotPorted) {
			t.Fatalf("%s: must still reach the refusal path, got %v", name, err)
		}
	}
	t.Run("associate draft born_date", func(t *testing.T) {
		drafts := NewAssociateDrafts(nil, func() time.Time { return now }, nil)
		_, err := drafts.associateDraftValidity(domain.RequestObject{{Name: "associate",
			Value: domain.RequestObject{{Name: "born_date", Value: refused}}}})
		check(t, "associate draft", err)
		if errors.Is(err, ErrAssociateDraftFailed) {
			t.Fatalf("associate draft: the refusal is not the 500 page: %v", err)
		}
	})
	t.Run("instructor report start_date", func(t *testing.T) {
		_, err := instructorReportParse(refused, now)
		check(t, "instructor report", err)
		if errors.Is(err, ErrInstructorReportNotPorted) || errors.Is(err, ErrInstructorReportServer) {
			t.Fatalf("instructor report: expected pydateutil's sentinel, got %v", err)
		}
	})
	t.Run("subscription import date cell", func(t *testing.T) {
		_, _, err := subscriptionImportDate(refused, now)
		check(t, "subscription import", err)
		if errors.Is(err, ErrSubscriptionImportNotPorted) {
			t.Fatalf("subscription import: expected pydateutil's sentinel, got %v", err)
		}
	})
}
