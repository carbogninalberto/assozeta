package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type profileReaderStub struct {
	row       domain.LoginProfileSnapshot
	err       error
	afterRead func()
	calls     int
}

func (r *profileReaderStub) LoginProfile(context.Context, string) (domain.LoginProfileSnapshot, error) {
	r.calls++
	if r.afterRead != nil {
		r.afterRead()
	}
	return r.row, r.err
}

func TestLoginProfileDoesNotReturnPartialOrForeignData(t *testing.T) {
	for _, failure := range []string{"read failure", "wrong user", "deleted", "missing association", "cancel before read", "cancel after read"} {
		t.Run(failure, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			store := &profileReaderStub{row: domain.LoginProfileSnapshot{UserID: "target", Role: domain.RoleAthlete, Active: true}}
			switch failure {
			case "read failure":
				store.err = errors.New("database unavailable")
			case "wrong user":
				store.row.UserID = "foreign"
			case "deleted":
				store.row.Deleted = true
			case "missing association":
				store.row.Role = domain.RoleAssociation
			case "cancel before read":
				cancel()
			case "cancel after read":
				store.afterRead = cancel
			}
			result, err := NewLoginProfiles(store, time.Now).Read(ctx, "target")
			if err == nil || result != nil {
				t.Fatal("failed profile exposed partial or foreign data")
			}
			if failure == "cancel before read" && store.calls != 0 {
				t.Fatal("cancelled profile reached database")
			}
		})
	}
}

func TestLoginProfileThirtyDayBoundaryUsesElapsedTime(t *testing.T) {
	// DST/local date boundaries must not turn the strict UTC 30-day comparison
	// into a calendar-month or local-midnight comparison.
	now := time.Date(2026, 3, 31, 0, 30, 0, 0, time.UTC)
	present := "present"
	row := domain.LoginProfileSnapshot{Logo: &present, Denomination: &present, TaxCode: &present, BalanceSheetStartDay: 1, BalanceSheetStartMonth: 1}
	for _, tc := range []struct {
		offset time.Duration
		want   bool
	}{{-time.Nanosecond, false}, {0, false}, {time.Nanosecond, true}} {
		row.DateJoined = now.Add(-30 * 24 * time.Hour).Add(tc.offset)
		if got := requiresProfileWelcome(row, now.In(time.FixedZone("local", 2*3600))); got != tc.want {
			t.Fatalf("boundary offset %s: %v", tc.offset, got)
		}
	}
}

func TestLoginProfileOptionalOnboardingFailurePreservesBaseFields(t *testing.T) {
	association := "association"
	store := &profileReaderStub{row: domain.LoginProfileSnapshot{UserID: "target", Role: domain.RoleAssociation, Active: true,
		AssociationID: &association, FirstName: "First", GoogleSyncEnabled: true, Onboarding: []domain.ProfileOnboarding{{ID: "partial"}}},
		err: errors.Join(domain.ErrProfileOnboardingUnavailable, errors.New("query failed"))}
	result, err := NewLoginProfiles(store, time.Now).Read(context.Background(), "target")
	if err != nil || result == nil || result.Onboarding != nil || result.FirstName != "First" || !result.GoogleSyncEnabled || !result.CanImpersonate {
		t.Fatal("optional failure changed base profile or exposed partial onboarding")
	}
	store.row.UserID = "foreign"
	if result, err = NewLoginProfiles(store, time.Now).Read(context.Background(), "target"); result != nil || !errors.Is(err, domain.ErrAccountNotFound) {
		t.Fatal("optional failure bypassed snapshot identity check")
	}
}
