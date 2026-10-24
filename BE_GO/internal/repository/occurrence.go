package repository

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jmoiron/sqlx"
)

type Occurrences struct{ db *sqlx.DB }

func NewOccurrences(db *sqlx.DB) *Occurrences { return &Occurrences{db: db} }

type periodicSource struct {
	ID            int64         `db:"id"`
	Name          string        `db:"name"`
	Task          string        `db:"task"`
	Enabled       bool          `db:"enabled"`
	Last          sql.NullTime  `db:"last_run_at"`
	Changed       time.Time     `db:"date_changed"`
	Start         sql.NullTime  `db:"start_time"`
	Count         int64         `db:"total_run_count"`
	Interval      sql.NullInt64 `db:"interval_id"`
	Cron          sql.NullInt64 `db:"crontab_id"`
	Solar         sql.NullInt64 `db:"solar_id"`
	Clocked       sql.NullInt64 `db:"clocked_id"`
	Args          string        `db:"args"`
	Kwargs        string        `db:"kwargs"`
	Headers       string        `db:"headers"`
	Queue         string        `db:"queue"`
	Exchange      string        `db:"exchange"`
	RoutingKey    string        `db:"routing_key"`
	Priority      sql.NullInt64 `db:"priority"`
	Expires       sql.NullTime  `db:"expires"`
	ExpireSeconds sql.NullInt64 `db:"expire_seconds"`
	OneOff        bool          `db:"one_off"`
}
type occurrenceTransaction struct {
	tx         *sqlx.Tx
	lifetime   *ownedTransaction
	jobs       *Jobs
	source     periodicSource
	interval   *domain.PeriodicInterval
	cron       *domain.PeriodicCron
	generation int64
	now        time.Time
	published  bool
}

func occurrenceError(err error) error {
	var pg *pgconn.PgError
	if errors.As(err, &pg) && pg.Code == "55P03" {
		return domain.ErrOccurrenceBusy
	}
	return err
}

func (r *Occurrences) BeginOccurrence(ctx context.Context, id int64) (domain.OccurrenceTransaction, error) {
	if id < 1 || id > math.MaxInt32 {
		return nil, domain.ErrScheduleInvalid
	}
	lifetime, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	tx := lifetime.tx
	fail := func(err error) (domain.OccurrenceTransaction, error) {
		_ = lifetime.Rollback()
		return nil, occurrenceError(err)
	}
	t := &occurrenceTransaction{tx: tx, lifetime: lifetime, jobs: NewJobs(r.db)}
	// Separate statements establish lock order without relying on join planning.
	err = tx.GetContext(ctx, &t.generation, `SELECT generation FROM public.assozeta_go_job_ownership WHERE singleton=1 AND runtime='go' FOR SHARE`)
	if errors.Is(err, sql.ErrNoRows) {
		return fail(domain.ErrOccurrenceInactive)
	}
	if err != nil {
		return fail(err)
	}
	err = tx.GetContext(ctx, &t.source, `SELECT id,name,task,enabled,last_run_at,date_changed,start_time,total_run_count,
 interval_id,crontab_id,solar_id,clocked_id,args,kwargs,COALESCE(headers,'') AS headers,
 COALESCE(queue,'') AS queue,COALESCE(exchange,'') AS exchange,COALESCE(routing_key,'') AS routing_key,
 priority,expires,expire_seconds,one_off FROM public.django_celery_beat_periodictask WHERE id=$1 FOR UPDATE NOWAIT`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return fail(domain.ErrOccurrenceNotFound)
	}
	if err != nil {
		return fail(err)
	}
	// Editors may lock a definition before its parent. Never wait on that reversed
	// order while holding the parent; roll back and let a later tick reevaluate.
	if t.source.Interval.Valid {
		t.interval = &domain.PeriodicInterval{}
		err = tx.QueryRowContext(ctx, `SELECT every,period FROM public.django_celery_beat_intervalschedule WHERE id=$1 FOR SHARE NOWAIT`, t.source.Interval.Int64).Scan(&t.interval.Every, &t.interval.Period)
		if err != nil {
			return fail(err)
		}
	}
	if t.source.Cron.Valid {
		t.cron = &domain.PeriodicCron{}
		err = tx.QueryRowContext(ctx, `SELECT minute,hour,day_of_week,day_of_month,month_of_year,timezone FROM public.django_celery_beat_crontabschedule WHERE id=$1 FOR SHARE NOWAIT`, t.source.Cron.Int64).Scan(&t.cron.Minute, &t.cron.Hour, &t.cron.DayOfWeek, &t.cron.DayOfMonth, &t.cron.MonthOfYear, &t.cron.Timezone)
		if err != nil {
			return fail(err)
		}
	}
	if err = tx.GetContext(ctx, &t.now, `SELECT clock_timestamp()`); err != nil {
		return fail(err)
	}
	return t, nil
}

func optionalTime(value sql.NullTime) *time.Time {
	if !value.Valid {
		return nil
	}
	v := value.Time
	return &v
}
func optionalInt(value sql.NullInt64) *int {
	if !value.Valid {
		return nil
	}
	v := int(value.Int64)
	return &v
}
func (t *occurrenceTransaction) Snapshot() domain.OccurrenceSnapshot {
	s := t.source
	row := domain.PeriodicSchedule{Task: s.Task, Enabled: s.Enabled, LastRunAt: optionalTime(s.Last), DateChanged: s.Changed,
		StartTime: optionalTime(s.Start), HasSolar: s.Solar.Valid, HasClocked: s.Clocked.Valid, Args: json.RawMessage(s.Args), Kwargs: json.RawMessage(s.Kwargs), Headers: json.RawMessage(s.Headers),
		Queue: s.Queue, Exchange: s.Exchange, RoutingKey: s.RoutingKey, Priority: optionalInt(s.Priority), ExpiresAt: optionalTime(s.Expires), ExpireSeconds: optionalInt(s.ExpireSeconds), OneOff: s.OneOff}
	if t.interval != nil {
		value := *t.interval
		row.Interval = &value
	}
	if t.cron != nil {
		value := *t.cron
		row.Cron = &value
	}
	return domain.OccurrenceSnapshot{SourceID: s.ID, SourceName: s.Name, TotalRunCount: s.Count, OwnershipGeneration: t.generation, Now: t.now, Schedule: row}
}

func (t *occurrenceTransaction) Publish(ctx context.Context) (domain.OccurrenceReceipt, error) {
	var receipt domain.OccurrenceReceipt
	if t.published || !t.source.Enabled || t.source.Task != domain.JobResetDailyEmailBalance {
		return receipt, domain.ErrJobConflict
	}
	var previous int64
	err := t.tx.GetContext(ctx, &previous, `SELECT dispatch_sequence FROM public.assozeta_go_periodic_occurrences WHERE periodic_task_id=$1 ORDER BY dispatch_sequence DESC LIMIT 1`, t.source.ID)
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return receipt, err
	}
	if previous == math.MaxInt64 {
		return receipt, domain.ErrJobConflict
	}
	sequence := previous + 1
	definition := t.Snapshot().Schedule
	definition.LastRunAt = nil
	definition.DateChanged = time.Time{}
	raw, err := json.Marshal(definition)
	if err != nil {
		return receipt, err
	}
	digest := sha256.Sum256(raw)
	// Three total attempts bound operational crash recovery; this is not Celery
	// business retry parity. The daily-reset service still handles UPDATE errors.
	job, err := t.jobs.Enqueue(ctx, t.tx, domain.JobIntent{ID: uuid.New(), AssociationID: uuid.Nil, Kind: domain.JobResetDailyEmailBalance, Version: 1,
		Key: fmt.Sprintf("periodic:%d:%d", t.source.ID, sequence), Payload: json.RawMessage(`{}`), AvailableAt: t.now, MaxAttempts: 3})
	if err != nil {
		return receipt, err
	}
	receipt = domain.OccurrenceReceipt{ID: uuid.New(), SourceID: t.source.ID, SourceName: t.source.Name, Sequence: sequence, JobID: job.ID, DispatchedAt: t.now, OwnershipGeneration: t.generation, DefinitionDigest: hex.EncodeToString(digest[:])}
	_, err = t.tx.ExecContext(ctx, `INSERT INTO public.assozeta_go_periodic_occurrences
 (id,periodic_task_id,source_name,dispatch_sequence,job_id,dispatched_at,ownership_generation,definition_sha256) VALUES($1,$2,$3,$4,$5,$6,$7,$8)`,
		receipt.ID, receipt.SourceID, receipt.SourceName, receipt.Sequence, receipt.JobID, receipt.DispatchedAt, receipt.OwnershipGeneration, receipt.DefinitionDigest)
	if err != nil {
		return domain.OccurrenceReceipt{}, err
	}
	// ModelEntry.save uses no_changes=True (a Python attribute, not a column).
	// Advance the existing Django cursor/date_changed but do not touch its marker.
	_, err = t.tx.ExecContext(ctx, `UPDATE public.django_celery_beat_periodictask SET last_run_at=$2,total_run_count=total_run_count+1,date_changed=clock_timestamp() WHERE id=$1`, t.source.ID, t.now)
	if err != nil {
		return domain.OccurrenceReceipt{}, err
	}
	t.published = true
	return receipt, nil
}
func (t *occurrenceTransaction) Commit() error   { return t.lifetime.Commit() }
func (t *occurrenceTransaction) Rollback() error { return t.lifetime.Rollback() }
