package service

import (
	"context"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

const raceSubscription = "a1000000-0000-4000-8000-000000000401"

// lifecycleRaceStore hands the service the transaction the test scripts.
type lifecycleRaceStore struct {
	tx domain.SubscriptionLifecycleTransaction
}

func (s lifecycleRaceStore) BeginSubscriptionLifecycle(context.Context) (domain.SubscriptionLifecycleTransaction, error) {
	return s.tx, nil
}

// lifecycleRaceFake scripts Archive's reads: one period payment is selected by
// the range lookup, then the payment read misses it, as when a concurrent
// request committed its delete between the two reads. The embedded interface
// panics on any effect the skip must not perform.
type lifecycleRaceFake struct {
	domain.SubscriptionLifecycleTransaction
	events    *[]string
	committed bool
}

func (f *lifecycleRaceFake) LifecycleAssociation(context.Context, string, *string) (string, error) {
	return "a1000000-0000-4000-8000-000000000402", nil
}

func (f *lifecycleRaceFake) Subscription(context.Context, string, *string, *string) (*domain.SubscriptionLifecycleRow, error) {
	start := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	end := time.Date(2026, 12, 31, 0, 0, 0, 0, time.UTC)
	return &domain.SubscriptionLifecycleRow{ID: raceSubscription, AssociationID: "a1000000-0000-4000-8000-000000000402",
		Start: &start, End: &end, Created: time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC), StartDay: 1, StartMonth: 1,
		Duration: 1}, nil
}

func (f *lifecycleRaceFake) ActorAssociation(context.Context, string) (*string, error) {
	return nil, nil
}

func (f *lifecycleRaceFake) LiveCourseSubscriptions(context.Context, string, *string) ([]domain.CourseSubscriptionRow, error) {
	return nil, nil
}

func (f *lifecycleRaceFake) SaveSubscription(context.Context, domain.SubscriptionLifecycleSave, domain.CourseSubscriptionAudit) error {
	*f.events = append(*f.events, "save-subscription")
	return nil
}

func (f *lifecycleRaceFake) RangePayments(context.Context, string, *string, time.Time, time.Time, *string) ([]string, error) {
	*f.events = append(*f.events, "range")
	return []string{"a1000000-0000-4000-8000-000000000403"}, nil
}

func (f *lifecycleRaceFake) Payment(context.Context, string) (*domain.SubscriptionLifecyclePayment, error) {
	*f.events = append(*f.events, "payment")
	return nil, nil
}

func (f *lifecycleRaceFake) Commit() error {
	*f.events = append(*f.events, "commit")
	f.committed = true
	return nil
}

func (f *lifecycleRaceFake) Rollback() error {
	if !f.committed {
		*f.events = append(*f.events, "rollback")
	}
	return nil
}

// TestArchiveSkipsPaymentDeletedBetweenReads pins owner decision X17: a
// payment the range lookup selected and the read no longer finds committed its
// delete before Django's iterator reached it, so the run continues (no archive
// save, no audit entry, no error) and commits the rest of the work.
func TestArchiveSkipsPaymentDeletedBetweenReads(t *testing.T) {
	events := []string{}
	fake := &lifecycleRaceFake{events: &events}
	lifecycles := NewSubscriptionLifecycles(lifecycleRaceStore{tx: fake}, func() time.Time {
		return time.Date(2026, 2, 1, 12, 0, 0, 0, time.UTC)
	})
	identity := domain.ResolvedIdentity{
		Actor:     &domain.Account{ID: "a1000000-0000-4000-8000-000000000405"},
		Effective: &domain.Account{ID: "a1000000-0000-4000-8000-000000000405"},
		ScopeUser: &domain.Account{ID: "a1000000-0000-4000-8000-000000000405"},
	}
	if err := lifecycles.Archive(context.Background(), identity, raceSubscription, "", domain.LoginAuditContext{}); err != nil {
		t.Fatalf("payment deleted between the two reads must be skipped: %v", err)
	}
	if !fake.committed {
		t.Fatalf("later work was not committed: %#v", events)
	}
	want := []string{"save-subscription", "range", "payment", "commit"}
	if len(events) != len(want) {
		t.Fatalf("effects %#v, want %#v", events, want)
	}
	for index := range want {
		if events[index] != want[index] {
			t.Fatalf("effects %#v, want %#v", events, want)
		}
	}
}
