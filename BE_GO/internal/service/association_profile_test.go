package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type associationProfileStub struct {
	row    domain.AssociationProfileSnapshot
	err    error
	cancel context.CancelFunc
	calls  int
}

func (r *associationProfileStub) AssociationProfile(context.Context, string) (domain.AssociationProfileSnapshot, error) {
	r.calls++
	if r.cancel != nil {
		r.cancel()
	}
	return r.row, r.err
}
func TestAssociationProfileScopeAndFailure(t *testing.T) {
	for _, failure := range []string{"query failure", "foreign owner", "foreign group", "missing association", "cancel before read", "cancel after read"} {
		t.Run(failure, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			store := &associationProfileStub{row: domain.AssociationProfileSnapshot{ID: "association", OwnerID: "owner"}}
			switch failure {
			case "query failure":
				store.err = errors.New("database unavailable")
			case "foreign owner":
				store.row.OwnerID = "foreign"
			case "foreign group":
				store.row.Groups = []domain.AssociationProfileGroup{{ID: "group", AssociationID: "foreign"}}
			case "missing association":
				store.row.ID = ""
			case "cancel before read":
				cancel()
			case "cancel after read":
				store.cancel = cancel
			}
			result, err := NewAssociationProfiles(store, time.Now).Read(ctx, "owner")
			if result != nil || err == nil {
				t.Fatal("failed or foreign profile returned data")
			}
			if failure == "cancel before read" && store.calls != 0 {
				t.Fatal("cancelled request read database")
			}
		})
	}
}
func TestAssociationProfileReviewedBoundary(t *testing.T) {
	now := time.Date(2026, 3, 31, 0, 30, 0, 0, time.UTC)
	for _, tc := range []struct {
		offset         time.Duration
		reviewed, want bool
	}{{-time.Microsecond, false, false}, {0, false, false}, {time.Microsecond, false, true}, {0, true, true}} {
		store := &associationProfileStub{row: domain.AssociationProfileSnapshot{ID: "association", OwnerID: "owner", Reviewed: tc.reviewed, OwnerJoined: now.Add(-5 * 24 * time.Hour).Add(tc.offset)}}
		result, err := NewAssociationProfiles(store, func() time.Time { return now.In(time.FixedZone("local", 2*3600)) }).Read(context.Background(), "owner")
		if err != nil || result.Reviewed != tc.want || result.Groups == nil {
			t.Fatal("reviewed boundary or empty group array differs")
		}
	}
}
