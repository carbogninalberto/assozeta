package main

import (
	"context"
	"errors"
	"fmt"
	"strconv"
	"sync/atomic"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handoff"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

func seed(ctx context.Context, db *sqlx.DB) error {
	now, err := scheduleNow()
	if err != nil {
		return err
	}
	tx, err := db.BeginTxx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = tx.ExecContext(ctx, "SELECT set_config('assozeta.scheduler_now',$1,true)", now.Format(time.RFC3339Nano)); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, "SELECT * FROM public.assozeta_seed_periodic_definitions('go',$1::jsonb)", string(declarations)); err != nil {
		return err
	}
	return tx.Commit()
}

type authorityTicker struct {
	lastGeneration int64
	broker         *redis.Client
	db             *sqlx.DB
	holder         string
	lease          time.Duration
	generation     atomic.Int64 // positive for our lease, zero for another runtime/freeze, negative while waiting
	next           domain.ScheduleTicker
	last           string
	instant        time.Time
}

func (t *authorityTicker) Tick(ctx context.Context, cursor domain.ScheduleCursor) (domain.SchedulePageResult, error) {
	if cursor.ThroughID == 0 {
		now, err := scheduleNow()
		if err != nil {
			return domain.SchedulePageResult{}, err
		}
		t.instant = now
	}
	var res domain.SchedulePageResult
	var err error
	gen := t.generation.Load()
	key := strconv.FormatInt(t.instant.UnixMilli(), 10)
	if controlledClock() && cursor.ThroughID == 0 && key == t.last && gen == t.lastGeneration {
		return domain.SchedulePageResult{EndOfRange: true}, nil
	}
	switch {
	case gen < 0:
		return res, nil
	case gen == 0:
		res.EndOfRange = true
		err = domain.ErrOccurrenceInactive
	default:
		lease := repository.ScheduleLease{Holder: t.holder, Generation: gen}
		if controlledClock() {
			lease.Now = t.instant
		}
		ctx = repository.WithScheduleLease(ctx, lease)
		res, err = t.next.Tick(ctx, cursor)
		if err == nil {
			push, cancel := context.WithTimeout(ctx, t.lease)
			err = t.pushPending(push, lease)
			cancel()
		}
	}
	if (res.EndOfRange && err == nil) || errors.Is(err, domain.ErrOccurrenceInactive) {
		if controlledClock() && (key != t.last || gen != t.lastGeneration) {
			handoff.Barrier("schedule:ticked", key)
			t.last = key
			t.lastGeneration = gen
		}
	}
	return res, err
}

func (t *authorityTicker) heartbeat(ctx context.Context) error {
	var operation *uuid.UUID
	if err := t.db.GetContext(ctx, &operation, "SELECT operation_id FROM public.assozeta_handoff_heartbeat($1,'go','scheduler')", t.holder); err != nil {
		return err
	}
	var outcome string
	var generation *int64
	err := t.db.QueryRowContext(ctx, "SELECT * FROM public.assozeta_scheduler_acquire('go',$1,$2)", t.holder, t.lease.Seconds()).Scan(&outcome, &generation)
	if err != nil {
		return err
	}
	switch outcome {
	case "acquired", "renewed":
		t.generation.Store(*generation)
	case "frozen", "not_owner":
		t.generation.Store(0)
		if operation != nil && outcome == "frozen" {
			_, err = t.db.ExecContext(ctx, "SELECT public.assozeta_handoff_acknowledge_freeze($1,$2)", t.holder, operation)
		}
	default:
		t.generation.Store(-1)
	}
	return err
}

func (t *authorityTicker) renew(ctx context.Context, frequency time.Duration) {
	timer := time.NewTicker(frequency)
	defer timer.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-timer.C:
			beat, cancel := context.WithTimeout(ctx, t.lease)
			if t.heartbeat(beat) != nil {
				t.generation.Store(-1)
			}
			cancel()
		}
	}
}

type authorityPublisher struct {
	db    *sqlx.DB
	store *repository.Occurrences
	next  domain.OccurrencePublisher
}

func (p authorityPublisher) PublishNext(ctx context.Context, id int64) (domain.OccurrenceResult, error) {
	lease, scoped := repository.CurrentScheduleLease(ctx)
	if !scoped {
		return p.next.PublishNext(ctx, id)
	}
	probe, err := p.store.BeginOccurrence(ctx, id)
	if err != nil {
		return domain.OccurrenceResult{}, err
	}
	row := probe.Snapshot()
	due, err := service.EvaluateScheduleDue(row.Schedule, row.Now)
	if rollback := probe.Rollback(); rollback != nil {
		return domain.OccurrenceResult{}, rollback
	}
	if err != nil || !due.Due {
		return domain.OccurrenceResult{NextCheckAfter: due.NextCheckAfter}, err
	}
	key := fmt.Sprintf("%d-%d", lease.Generation, id)
	handoff.Barrier("schedule:due", key)
	res, err := p.next.PublishNext(ctx, id)
	if err != nil && ctx.Err() != nil {
		// A suspended process can outlive both its page deadline and its lease.
		// Check the fence with a fresh, bounded read. Never publish on this path.
		check, cancel := context.WithTimeout(context.WithoutCancel(ctx), time.Second)
		defer cancel()
		var owner int64
		err = p.db.GetContext(check, &owner, "SELECT public.assozeta_scheduler_fence($1,$2)", lease.Holder, lease.Generation)
		var stale *pgconn.PgError
		if errors.As(err, &stale) && stale.Code == "AZF02" {
			handoff.Barrier("schedule:fenced", key)
		}
		return domain.OccurrenceResult{}, ctx.Err()
	}
	var pg *pgconn.PgError
	if errors.As(err, &pg) && pg.Code == "AZF02" {
		handoff.Barrier("schedule:fenced", key)
		return res, domain.ErrOccurrenceInactive
	}
	if res.Published {
		handoff.Barrier("schedule:committed", strconv.FormatInt(id, 10))
	}
	return res, err
}
