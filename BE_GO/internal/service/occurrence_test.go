package service

import (
	"context"
	"errors"
	"io"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type occurrenceFixture struct {
	events   []string
	fail     string
	snapshot domain.OccurrenceSnapshot
}

func (f *occurrenceFixture) Acquire(_ context.Context, exclusive, required bool) (io.Closer, error) {
	if exclusive || !required {
		panic("occurrence requires the shared lifecycle lease")
	}
	f.events = append(f.events, "lease")
	if f.fail == "lease" {
		return nil, domain.ErrRestoreBusy
	}
	return occurrenceLease{f}, nil
}

type occurrenceLease struct{ f *occurrenceFixture }

func (l occurrenceLease) Close() error { l.f.events = append(l.f.events, "release"); return nil }
func (f *occurrenceFixture) Pending(context.Context) (bool, error) {
	f.events = append(f.events, "barrier")
	return f.fail == "pending", nil
}
func (f *occurrenceFixture) BeginOccurrence(context.Context, int64) (domain.OccurrenceTransaction, error) {
	f.events = append(f.events, "begin")
	if f.fail == "owner" {
		return nil, domain.ErrOccurrenceInactive
	}
	return f, nil
}
func (f *occurrenceFixture) Snapshot() domain.OccurrenceSnapshot { return f.snapshot }
func (f *occurrenceFixture) Publish(context.Context) (domain.OccurrenceReceipt, error) {
	f.events = append(f.events, "publish")
	if f.fail == "publish" {
		return domain.OccurrenceReceipt{}, errors.New("fixture")
	}
	return domain.OccurrenceReceipt{Sequence: 1}, nil
}
func (f *occurrenceFixture) Commit() error {
	f.events = append(f.events, "commit")
	if f.fail == "commit" {
		return errors.New("fixture")
	}
	return nil
}
func (f *occurrenceFixture) Rollback() error { f.events = append(f.events, "rollback"); return nil }

func TestOccurrencePublisherRetainsLeaseThroughTransaction(t *testing.T) {
	for _, fault := range []string{"", "lease", "pending", "owner", "unsupported", "not-due", "publish", "commit"} {
		t.Run(fault, func(t *testing.T) {
			now := time.Date(2026, 10, 22, 12, 0, 0, 0, time.UTC)
			last := now.Add(-time.Hour)
			f := &occurrenceFixture{fail: fault, snapshot: domain.OccurrenceSnapshot{Now: now, Schedule: domain.PeriodicSchedule{Task: domain.JobResetDailyEmailBalance, Enabled: true, LastRunAt: &last, Interval: &domain.PeriodicInterval{Every: 60, Period: "seconds"}}}}
			if fault == "unsupported" {
				f.snapshot.Schedule.Queue = "custom"
			}
			if fault == "not-due" {
				f.snapshot.Schedule.LastRunAt = &now
			}
			result, err := NewOccurrencePublisher(f, f, f).PublishNext(context.Background(), 1)
			if (err == nil) != (fault == "" || fault == "not-due") {
				t.Fatalf("fault %s err %v", fault, err)
			}
			if result.Published != (fault == "") {
				t.Fatal("uncommitted publication reported", result)
			}
			if fault != "lease" && f.events[len(f.events)-1] != "release" {
				t.Fatal("lease not held through outcome", f.events)
			}
			for i, event := range f.events {
				if event == "publish" && (i < 3 || f.events[0] != "lease" || f.events[1] != "barrier") {
					t.Fatal(f.events)
				}
			}
			if fault == "unsupported" || fault == "not-due" {
				for _, event := range f.events {
					if event == "publish" || event == "commit" {
						t.Fatal("unsupported or early mutation", f.events)
					}
				}
			}
		})
	}
}
