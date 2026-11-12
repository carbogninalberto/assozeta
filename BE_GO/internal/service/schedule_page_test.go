package service

import (
	"context"
	"errors"
	"math"
	"reflect"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type pageStoreFunc func(context.Context, int64, int64, int) ([]domain.PeriodicCandidate, error)

func (f pageStoreFunc) ListCandidates(ctx context.Context, after, through int64, limit int) ([]domain.PeriodicCandidate, error) {
	return f(ctx, after, through, limit)
}
func (f pageStoreFunc) HighWater(context.Context) (int64, error) { return 100, nil }

type pagePublishFunc func(context.Context, int64) (domain.OccurrenceResult, error)

func (f pagePublishFunc) PublishNext(ctx context.Context, id int64) (domain.OccurrenceResult, error) {
	return f(ctx, id)
}
func pageCandidate(id int64) domain.PeriodicCandidate {
	return domain.PeriodicCandidate{ID: id, Task: domain.JobResetDailyEmailBalance, Enabled: true}
}

func TestSchedulePageDefaultsAndBounds(t *testing.T) {
	calls := 0
	store := pageStoreFunc(func(ctx context.Context, after, through int64, limit int) ([]domain.PeriodicCandidate, error) {
		calls++
		if after != 9 || limit != 16 {
			t.Fatalf("unexpected query %d %d", after, limit)
		}
		return nil, nil
	})
	pub := pagePublishFunc(func(context.Context, int64) (domain.OccurrenceResult, error) {
		t.Fatal("unexpected publish")
		return domain.OccurrenceResult{}, nil
	})
	pager, err := NewSchedulePager(store, pub, SchedulePageOptions{})
	if err != nil {
		t.Fatal(err)
	}
	result, err := pager.Tick(context.Background(), domain.ScheduleCursor{AfterID: 9, ThroughID: 100})
	if err != nil || calls != 1 || result.NextCursor != (domain.ScheduleCursor{}) || !result.EndOfRange || len(result.Outcomes) != 0 {
		t.Fatalf("%+v %v calls%d", result, err, calls)
	}
	for _, after := range []int64{-1, math.MaxInt32 + 1} {
		if _, err := pager.Tick(context.Background(), domain.ScheduleCursor{AfterID: after, ThroughID: 100}); !errors.Is(err, domain.ErrSchedulePageInvalid) {
			t.Fatal(err)
		}
	}
	if calls != 1 {
		t.Fatal("invalid cursor queried store")
	}
	for _, options := range []SchedulePageOptions{
		{Limit: -1}, {Limit: 65}, {TickTimeout: -1}, {SourceTimeout: -1},
		{TickTimeout: 6 * time.Second}, {TickTimeout: time.Microsecond},
		{SourceTimeout: time.Microsecond}, {TickTimeout: time.Second, SourceTimeout: 2 * time.Second},
	} {
		if _, err := NewSchedulePager(store, pub, options); !errors.Is(err, domain.ErrSchedulePageInvalid) {
			t.Fatalf("accepted %+v: %v", options, err)
		}
	}
	if _, err := NewSchedulePager(nil, pub, SchedulePageOptions{}); err == nil {
		t.Fatal("nil store")
	}
	if _, err := NewSchedulePager(store, nil, SchedulePageOptions{}); err == nil {
		t.Fatal("nil publisher")
	}
}

func TestSchedulePageAdvancesPastEveryNonterminalOutcome(t *testing.T) {
	rows := []domain.PeriodicCandidate{}
	for i := int64(1); i <= 8; i++ {
		rows = append(rows, pageCandidate(i))
	}
	rows[0].Enabled = false
	rows[1].Task = "other"
	called := []int64{}
	pager, _ := NewSchedulePager(pageStoreFunc(func(context.Context, int64, int64, int) ([]domain.PeriodicCandidate, error) { return rows, nil }),
		pagePublishFunc(func(ctx context.Context, id int64) (domain.OccurrenceResult, error) {
			called = append(called, id)
			switch id {
			case 3:
				return domain.OccurrenceResult{}, domain.ErrScheduleUnsupported
			case 4:
				return domain.OccurrenceResult{}, domain.ErrOccurrenceBusy
			case 5:
				return domain.OccurrenceResult{}, domain.ErrOccurrenceNotFound
			case 6:
				return domain.OccurrenceResult{NextCheckAfter: 25 * time.Hour}, nil
			case 7:
				return domain.OccurrenceResult{Published: true}, nil
			default:
				return domain.OccurrenceResult{}, domain.ErrScheduleInvalid
			}
		}), SchedulePageOptions{Limit: 8})
	result, err := pager.Tick(context.Background(), domain.ScheduleCursor{})
	if err != nil || result.NextCursor != (domain.ScheduleCursor{AfterID: 8, ThroughID: 100}) || result.EndOfRange {
		t.Fatalf("%+v %v", result, err)
	}
	want := []domain.SchedulePageStatus{domain.SchedulePageDisabled, domain.SchedulePageUnrelated, domain.SchedulePageUnsupported, domain.SchedulePageBusy, domain.SchedulePageNotFound, domain.SchedulePageWaiting, domain.SchedulePagePublished, domain.SchedulePageUnsupported}
	for i, outcome := range result.Outcomes {
		if outcome.SourceID != int64(i+1) || outcome.Status != want[i] {
			t.Fatalf("%+v", result)
		}
	}
	if len(result.Outcomes) != 8 || !reflect.DeepEqual(called, []int64{3, 4, 5, 6, 7, 8}) || result.Outcomes[5].NextCheckAfter != 25*time.Hour {
		t.Fatalf("%+v %v", result, called)
	}
}

func TestSchedulePageTerminalErrorsKeepLastAttemptedCursor(t *testing.T) {
	for _, item := range []struct {
		err    error
		status domain.SchedulePageStatus
	}{
		{domain.ErrOccurrenceInactive, domain.SchedulePageInactive},
		{domain.ErrRestoreBusy, domain.SchedulePageRestoreBlocked},
		{domain.ErrRestorePending, domain.SchedulePageRestoreBlocked},
		{domain.ErrRestoreVolumeUnavailable, domain.SchedulePageRestoreBlocked},
		{domain.ErrRestoreLockRequired, domain.SchedulePageRestoreBlocked},
		{context.Canceled, domain.SchedulePageCancelled},
		{context.DeadlineExceeded, domain.SchedulePageCancelled},
		{errors.New("ambiguous commit failure"), domain.SchedulePageFailed},
	} {
		t.Run(string(item.status)+item.err.Error(), func(t *testing.T) {
			calls := 0
			pager, _ := NewSchedulePager(pageStoreFunc(func(context.Context, int64, int64, int) ([]domain.PeriodicCandidate, error) {
				return []domain.PeriodicCandidate{pageCandidate(4), pageCandidate(9)}, nil
			}),
				pagePublishFunc(func(context.Context, int64) (domain.OccurrenceResult, error) {
					calls++
					return domain.OccurrenceResult{}, item.err
				}), SchedulePageOptions{})
			result, err := pager.Tick(context.Background(), domain.ScheduleCursor{AfterID: 2, ThroughID: 100})
			if !errors.Is(err, item.err) || calls != 1 || result.NextCursor != (domain.ScheduleCursor{AfterID: 4, ThroughID: 100}) || result.EndOfRange || len(result.Outcomes) != 1 || result.Outcomes[0].Status != item.status {
				t.Fatalf("%+v %v calls%d", result, err, calls)
			}
		})
	}
}

func TestSchedulePageRefreshesMetadataAndWrapsOnlyConsumedPage(t *testing.T) {
	queries := 0
	publishes := 0
	pager, _ := NewSchedulePager(pageStoreFunc(func(ctx context.Context, after, through int64, limit int) ([]domain.PeriodicCandidate, error) {
		queries++
		row := pageCandidate(8)
		row.Enabled = queries > 1
		return []domain.PeriodicCandidate{row}, nil
	}), pagePublishFunc(func(context.Context, int64) (domain.OccurrenceResult, error) {
		publishes++
		return domain.OccurrenceResult{Published: true}, nil
	}), SchedulePageOptions{})
	for i := 0; i < 2; i++ {
		result, err := pager.Tick(context.Background(), domain.ScheduleCursor{})
		if err != nil || !result.EndOfRange || result.NextCursor != (domain.ScheduleCursor{}) {
			t.Fatalf("%+v %v", result, err)
		}
	}
	if queries != 2 || publishes != 1 {
		t.Fatal(queries, publishes)
	}
}

func TestSchedulePageCancellationDoesNotSkipUnattemptedRows(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	calls := 0
	pager, _ := NewSchedulePager(pageStoreFunc(func(context.Context, int64, int64, int) ([]domain.PeriodicCandidate, error) {
		return []domain.PeriodicCandidate{pageCandidate(3), pageCandidate(8)}, nil
	}),
		pagePublishFunc(func(context.Context, int64) (domain.OccurrenceResult, error) {
			calls++
			cancel()
			return domain.OccurrenceResult{Published: true}, nil
		}), SchedulePageOptions{})
	result, err := pager.Tick(ctx, domain.ScheduleCursor{})
	if !errors.Is(err, context.Canceled) || calls != 1 || result.NextCursor != (domain.ScheduleCursor{AfterID: 3, ThroughID: 100}) || len(result.Outcomes) != 1 || result.EndOfRange {
		t.Fatalf("%+v %v", result, err)
	}
}

func TestSchedulePageDeadlinesBoundStoreAndPublisher(t *testing.T) {
	for _, storeBlocks := range []bool{false, true} {
		t.Run(map[bool]string{false: "publisher", true: "store"}[storeBlocks], func(t *testing.T) {
			var observed time.Duration
			pager, _ := NewSchedulePager(pageStoreFunc(func(ctx context.Context, after, through int64, limit int) ([]domain.PeriodicCandidate, error) {
				if storeBlocks {
					deadline, ok := ctx.Deadline()
					if !ok {
						t.Fatal("no store deadline")
					}
					observed = time.Until(deadline)
					<-ctx.Done()
					return nil, ctx.Err()
				}
				return []domain.PeriodicCandidate{pageCandidate(2)}, nil
			}), pagePublishFunc(func(ctx context.Context, id int64) (domain.OccurrenceResult, error) {
				deadline, ok := ctx.Deadline()
				if !ok {
					t.Fatal("no source deadline")
				}
				observed = time.Until(deadline)
				<-ctx.Done()
				return domain.OccurrenceResult{}, ctx.Err()
			}), SchedulePageOptions{TickTimeout: 50 * time.Millisecond, SourceTimeout: 10 * time.Millisecond})
			result, err := pager.Tick(context.Background(), domain.ScheduleCursor{})
			if !errors.Is(err, context.DeadlineExceeded) || observed <= 0 || observed > 50*time.Millisecond {
				t.Fatalf("%+v %v deadline%v", result, err, observed)
			}
			if storeBlocks && result.NextCursor != (domain.ScheduleCursor{ThroughID: 100}) || !storeBlocks && result.NextCursor != (domain.ScheduleCursor{AfterID: 2, ThroughID: 100}) {
				t.Fatalf("%+v", result)
			}
		})
	}
}

func TestSchedulePageRejectsMalformedStorePageBeforePublishing(t *testing.T) {
	for _, rows := range [][]domain.PeriodicCandidate{
		{pageCandidate(1)}, {pageCandidate(3), pageCandidate(3)}, {pageCandidate(4), pageCandidate(3)},
		{pageCandidate(math.MaxInt32 + 1)}, {pageCandidate(3), pageCandidate(4), pageCandidate(5)},
	} {
		pager, _ := NewSchedulePager(pageStoreFunc(func(context.Context, int64, int64, int) ([]domain.PeriodicCandidate, error) { return rows, nil }),
			pagePublishFunc(func(context.Context, int64) (domain.OccurrenceResult, error) {
				t.Fatal("malformed page published")
				return domain.OccurrenceResult{}, nil
			}), SchedulePageOptions{Limit: 2})
		result, err := pager.Tick(context.Background(), domain.ScheduleCursor{AfterID: 2, ThroughID: 100})
		if !errors.Is(err, domain.ErrSchedulePageCandidates) || result.NextCursor != (domain.ScheduleCursor{AfterID: 2, ThroughID: 100}) || len(result.Outcomes) != 0 {
			t.Fatalf("%+v %v", result, err)
		}
	}
}

type pageWaterStore struct {
	pageStoreFunc
	high func(context.Context) (int64, error)
}

func (s pageWaterStore) HighWater(ctx context.Context) (int64, error) { return s.high(ctx) }

func TestSchedulePageHighWaterClosesRotationDuringContinuousInsertions(t *testing.T) {
	maximum := int64(2)
	highCalls, listCalls := 0, 0
	visited := []int64{}
	store := pageWaterStore{high: func(context.Context) (int64, error) { highCalls++; return maximum, nil },
		pageStoreFunc: func(ctx context.Context, after, through int64, limit int) ([]domain.PeriodicCandidate, error) {
			listCalls++
			maximum++
			if through > maximum || limit != 1 {
				t.Fatal("invalid range")
			}
			if after < through {
				return []domain.PeriodicCandidate{pageCandidate(after + 1)}, nil
			}
			return nil, nil
		}}
	pager, _ := NewSchedulePager(store, pagePublishFunc(func(ctx context.Context, id int64) (domain.OccurrenceResult, error) {
		visited = append(visited, id)
		return domain.OccurrenceResult{}, nil
	}), SchedulePageOptions{Limit: 1})
	cursor := domain.ScheduleCursor{}
	for i := 0; i < 3; i++ {
		result, err := pager.Tick(context.Background(), cursor)
		if err != nil {
			t.Fatal(err)
		}
		cursor = result.NextCursor
	}
	if !reflect.DeepEqual(visited, []int64{1, 2, 1}) || highCalls != 2 || listCalls != 3 || cursor != (domain.ScheduleCursor{AfterID: 1, ThroughID: 4}) {
		t.Fatalf("%v high%d list%d cursor%+v", visited, highCalls, listCalls, cursor)
	}
}

func TestSchedulePagePreservesWatermarkAfterCaptureCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	store := pageWaterStore{high: func(context.Context) (int64, error) { cancel(); return 42, nil },
		pageStoreFunc: func(context.Context, int64, int64, int) ([]domain.PeriodicCandidate, error) {
			t.Fatal("cancelled page query")
			return nil, nil
		}}
	pager, _ := NewSchedulePager(store, pagePublishFunc(func(context.Context, int64) (domain.OccurrenceResult, error) {
		t.Fatal("publish")
		return domain.OccurrenceResult{}, nil
	}), SchedulePageOptions{})
	result, err := pager.Tick(ctx, domain.ScheduleCursor{})
	if !errors.Is(err, context.Canceled) || result.NextCursor != (domain.ScheduleCursor{ThroughID: 42}) || result.EndOfRange {
		t.Fatalf("%+v %v", result, err)
	}
}

func TestSchedulePageTerminalFailurePreservesEarlierSuccessAndHidesSecrets(t *testing.T) {
	secret := errors.New("postgres://fixture:private-password@private-host/db ambiguous commit")
	calls := 0
	pager, _ := NewSchedulePager(pageStoreFunc(func(context.Context, int64, int64, int) ([]domain.PeriodicCandidate, error) {
		return []domain.PeriodicCandidate{pageCandidate(3), pageCandidate(8), pageCandidate(9)}, nil
	}),
		pagePublishFunc(func(context.Context, int64) (domain.OccurrenceResult, error) {
			calls++
			if calls == 1 {
				return domain.OccurrenceResult{Published: true}, nil
			}
			return domain.OccurrenceResult{}, secret
		}), SchedulePageOptions{})
	result, err := pager.Tick(context.Background(), domain.ScheduleCursor{})
	if !errors.Is(err, secret) || err.Error() == secret.Error() || calls != 2 || result.NextCursor != (domain.ScheduleCursor{AfterID: 8, ThroughID: 100}) || result.EndOfRange || len(result.Outcomes) != 2 || result.Outcomes[0].Status != domain.SchedulePagePublished || result.Outcomes[1].Status != domain.SchedulePageFailed {
		t.Fatalf("%+v %v calls%d", result, err, calls)
	}
}

func TestSchedulePageRejectsTypedNilCollaborators(t *testing.T) {
	var missingStore pageStoreFunc
	var missingPublisher pagePublishFunc
	validStore := pageStoreFunc(func(context.Context, int64, int64, int) ([]domain.PeriodicCandidate, error) { return nil, nil })
	validPublisher := pagePublishFunc(func(context.Context, int64) (domain.OccurrenceResult, error) { return domain.OccurrenceResult{}, nil })
	if _, err := NewSchedulePager(missingStore, validPublisher, SchedulePageOptions{}); !errors.Is(err, domain.ErrSchedulePageInvalid) {
		t.Fatalf("typed nil store accepted: %v", err)
	}
	if _, err := NewSchedulePager(validStore, missingPublisher, SchedulePageOptions{}); !errors.Is(err, domain.ErrSchedulePageInvalid) {
		t.Fatalf("typed nil publisher accepted: %v", err)
	}
}
