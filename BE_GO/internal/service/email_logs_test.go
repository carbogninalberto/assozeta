package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestDjangoNaiveNowReadsWallClockAsUTC(t *testing.T) {
	rome, err := time.LoadLocation("Europe/Rome")
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range []struct{ instant, want string }{
		// CEST (+02:00) before the 2026-10-25 change, CET (+01:00) after.
		{"2026-10-24T10:00:00.1234567Z", "2026-10-24T12:00:00.123456Z"},
		{"2026-10-27T10:00:00Z", "2026-10-27T11:00:00Z"},
	} {
		instant, _ := time.Parse(time.RFC3339Nano, c.instant)
		if got := DjangoNaiveNow(instant, rome).Format(time.RFC3339Nano); got != c.want {
			t.Fatalf("%s: %s want %s", c.instant, got, c.want)
		}
	}
	instant := time.Date(2026, 10, 27, 10, 0, 0, 999, time.UTC)
	if got := DjangoNaiveNow(instant, time.UTC); !got.Equal(instant.Truncate(time.Microsecond)) {
		t.Fatal(got)
	}
}

type emailLogsStoreTest struct {
	owner        string
	since, today time.Time
}

func (s *emailLogsStoreTest) EmailLogs(_ context.Context, owner string, since, today time.Time) ([]domain.EmailLogRecord, int, error) {
	s.owner, s.since, s.today = owner, since, today
	return nil, 0, nil
}

func TestEmailLogsScopeAndWindows(t *testing.T) {
	store := &emailLogsStoreTest{}
	now := time.Date(2026, 10, 27, 10, 0, 0, 0, time.UTC)
	logs := NewEmailLogs(store, func() time.Time { return now }, time.UTC)
	owner := &domain.Account{ID: "owner", Role: domain.RoleAssociation}
	if _, err := logs.List(context.Background(), domain.ResolvedIdentity{ScopeUser: owner}); err != nil || store.owner != "owner" {
		t.Fatal(err, store.owner)
	}
	if !store.since.Equal(now.Add(-90*24*time.Hour)) || !store.today.Equal(now.Add(-24*time.Hour)) {
		t.Fatal(store.since, store.today)
	}
	connected := "owner"
	collaborator := &domain.Account{ID: "member", Role: domain.RoleCollaborator, ConnectedUserID: &connected}
	if _, err := logs.List(context.Background(), domain.ResolvedIdentity{ScopeUser: collaborator}); err != nil || store.owner != "owner" {
		t.Fatal(err, store.owner)
	}
	athlete := &domain.Account{ID: "athlete", Role: domain.RoleAthlete}
	if _, err := logs.List(context.Background(), domain.ResolvedIdentity{ScopeUser: athlete}); !errors.Is(err, domain.ErrEmailLogsNotAssociation) {
		t.Fatal(err)
	}
}
