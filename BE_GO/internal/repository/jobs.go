package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"regexp"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// DurableJobs is an Assozeta extension, not a Celery wire-protocol adapter.
// Services record intent in their business transaction. Execution is at least once.
type DurableJobs interface {
	Enqueue(context.Context, *sqlx.Tx, domain.JobIntent) (domain.Job, error)
	Claim(context.Context, []string, string, int, time.Duration) ([]domain.Job, error)
	Renew(context.Context, uuid.UUID, uuid.UUID, time.Duration) error
	Complete(context.Context, uuid.UUID, uuid.UUID, json.RawMessage) error
	CompleteInTx(context.Context, *sqlx.Tx, uuid.UUID, uuid.UUID, json.RawMessage) error
	Fail(context.Context, uuid.UUID, uuid.UUID, string, bool, time.Duration) error
	Get(context.Context, uuid.UUID, uuid.UUID) (domain.Job, error)
	CancelPending(context.Context, uuid.UUID, uuid.UUID) error
}

type Jobs struct{ db *sqlx.DB }

type JobExecutionStore interface {
	Claim(context.Context, []string, string, int, time.Duration) ([]domain.Job, error)
	Renew(context.Context, uuid.UUID, uuid.UUID, time.Duration) error
	Fail(context.Context, uuid.UUID, uuid.UUID, string, bool, time.Duration) error
	Get(context.Context, uuid.UUID, uuid.UUID) (domain.Job, error)
}

func NewJobs(db *sqlx.DB) *Jobs { return &Jobs{db: db} }

var jobKind = regexp.MustCompile(`^[a-z][a-z0-9_.]{0,127}$`)

const jobColumns = `id,association_id,kind,version,idempotency_key,payload,state,attempts,max_attempts,available_at,created_at,finished_at,lease_owner,lease_token,lease_until,error_code,result,ownership_generation`
const clearJobLease = `lease_owner='',lease_token='00000000-0000-0000-0000-000000000000',lease_until=NULL`

func validJobJSON(raw json.RawMessage) bool { return len(raw) <= 65536 && json.Valid(raw) }
func validLease(duration time.Duration) bool {
	return duration >= time.Millisecond && duration <= 24*time.Hour
}

func (r *Jobs) Enqueue(ctx context.Context, tx *sqlx.Tx, in domain.JobIntent) (domain.Job, error) {
	var job domain.Job
	if tx == nil || in.ID == uuid.Nil || !jobKind.MatchString(in.Kind) || in.Version < 1 || in.Version > 2147483647 || len(in.Key) < 1 || len(in.Key) > 512 || strings.ContainsRune(in.Key, 0) || !validJobJSON(in.Payload) || in.MaxAttempts < 1 || in.MaxAttempts > 100 {
		return job, domain.ErrJobInvalid
	}
	var available any
	if !in.AvailableAt.IsZero() {
		available = in.AvailableAt
	}
	err := tx.GetContext(ctx, &job, `INSERT INTO public.assozeta_go_jobs (id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at)
 VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,COALESCE($8::timestamptz,clock_timestamp()))
 ON CONFLICT (association_id,kind,idempotency_key) DO NOTHING RETURNING `+jobColumns, in.ID, in.AssociationID, in.Kind, in.Version, in.Key, string(in.Payload), in.MaxAttempts, available)
	if !errors.Is(err, sql.ErrNoRows) {
		return job, err
	}
	// A second statement sees the committed winner under READ COMMITTED. Higher
	// isolation may report serialization failure; the business service retries its
	// whole transaction rather than silently committing only the job.
	err = tx.GetContext(ctx, &job, `SELECT `+jobColumns+` FROM public.assozeta_go_jobs WHERE association_id=$1 AND kind=$2 AND idempotency_key=$3 AND version=$4 AND payload=$5::jsonb AND max_attempts=$6`, in.AssociationID, in.Kind, in.Key, in.Version, string(in.Payload), in.MaxAttempts)
	if errors.Is(err, sql.ErrNoRows) {
		return job, domain.ErrJobConflict
	}
	return job, err
}

func (r *Jobs) Claim(ctx context.Context, kinds []string, owner string, limit int, lease time.Duration) ([]domain.Job, error) {
	if len(kinds) == 0 || len(kinds) > 64 || len(owner) == 0 || len(owner) > 128 || strings.ContainsRune(owner, 0) || limit < 1 || limit > 64 || !validLease(lease) {
		return nil, domain.ErrJobInvalid
	}
	for _, kind := range kinds {
		if !jobKind.MatchString(kind) {
			return nil, domain.ErrJobInvalid
		}
	}
	jobs := []domain.Job{}
	err := r.db.SelectContext(ctx, &jobs, claimJobsSQL, kinds, limit, owner, uuid.New(), lease.Milliseconds())
	return jobs, err
}

func (r *Jobs) Renew(ctx context.Context, id, token uuid.UUID, lease time.Duration) error {
	if !validLease(lease) {
		return domain.ErrJobInvalid
	}
	result, err := r.db.ExecContext(ctx, goJobOwnershipCTE+`UPDATE public.assozeta_go_jobs j SET lease_until=GREATEST(lease_until,clock_timestamp()+make_interval(secs=>$3::double precision/1000))
 FROM ownership o WHERE id=$1 AND lease_token=$2 AND state='running' AND lease_until>clock_timestamp() AND j.ownership_generation=o.generation`, id, token, lease.Milliseconds())
	return jobLeaseResult(result, err)
}
func (r *Jobs) Complete(ctx context.Context, id, token uuid.UUID, result json.RawMessage) error {
	return completeJob(ctx, r.db, id, token, result)
}

// CompleteInTx lets a service commit database effects and completion atomically.
// Any error must roll back the caller's entire transaction. Provider effects still
// require an external idempotency boundary; a SQL lease is not exactly-once I/O.
func (r *Jobs) CompleteInTx(ctx context.Context, tx *sqlx.Tx, id, token uuid.UUID, result json.RawMessage) error {
	if tx == nil {
		return domain.ErrJobInvalid
	}
	return completeJob(ctx, tx, id, token, result)
}

func completeJob(ctx context.Context, executor sqlx.ExecerContext, id, token uuid.UUID, result json.RawMessage) error {
	var payload any
	if result != nil {
		if !validJobJSON(result) {
			return domain.ErrJobInvalid
		}
		payload = string(result)
	}
	changed, err := executor.ExecContext(ctx, goJobOwnershipCTE+`UPDATE public.assozeta_go_jobs j SET state='completed',finished_at=clock_timestamp(),result=$3::jsonb,error_code='',`+clearJobLease+`
 FROM ownership o WHERE id=$1 AND lease_token=$2 AND state='running' AND lease_until>clock_timestamp() AND j.ownership_generation=o.generation`, id, token, payload)
	return jobLeaseResult(changed, err)
}
func (r *Jobs) Fail(ctx context.Context, id, token uuid.UUID, code string, retry bool, delay time.Duration) error {
	if !jobKind.MatchString(code) || delay < 0 || delay > 30*24*time.Hour {
		return domain.ErrJobInvalid
	}
	changed, err := r.db.ExecContext(ctx, goJobOwnershipCTE+`UPDATE public.assozeta_go_jobs j SET
 state=CASE WHEN $4 AND attempts<max_attempts THEN 'pending' ELSE 'failed' END,
 finished_at=CASE WHEN $4 AND attempts<max_attempts THEN NULL ELSE clock_timestamp() END,
 available_at=CASE WHEN $4 AND attempts<max_attempts THEN clock_timestamp()+make_interval(secs=>$5::double precision/1000) ELSE available_at END,
 error_code=$3,`+clearJobLease+` FROM ownership o WHERE id=$1 AND lease_token=$2 AND state='running' AND lease_until>clock_timestamp() AND j.ownership_generation=o.generation`, id, token, code, retry, delay.Milliseconds())
	return jobLeaseResult(changed, err)
}
func jobLeaseResult(result sql.Result, err error) error {
	if err != nil {
		return err
	}
	n, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if n != 1 {
		return domain.ErrJobLeaseLost
	}
	return nil
}

func (r *Jobs) Get(ctx context.Context, association, id uuid.UUID) (domain.Job, error) {
	var job domain.Job
	err := r.db.GetContext(ctx, &job, `SELECT `+jobColumns+` FROM public.assozeta_go_jobs WHERE association_id=$1 AND id=$2`, association, id)
	if errors.Is(err, sql.ErrNoRows) {
		err = domain.ErrJobNotFound
	}
	return job, err
}

// CancelPending deliberately cannot claim to interrupt a running provider call.
// Running cancellation requires a cooperative executor and workflow policy.
func (r *Jobs) CancelPending(ctx context.Context, association, id uuid.UUID) error {
	result, err := r.db.ExecContext(ctx, `UPDATE public.assozeta_go_jobs SET state='cancelled',finished_at=COALESCE(finished_at,clock_timestamp()) WHERE association_id=$1 AND id=$2 AND state IN ('pending','cancelled')`, association, id)
	if err != nil {
		return err
	}
	n, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if n == 1 {
		return nil
	}
	_, err = r.Get(ctx, association, id)
	if err != nil {
		return err
	}
	return domain.ErrJobConflict
}

// Each kind/state branch locks at most limit rows before merging candidates.
// Extra locks last only through this statement, at most 2*limit*len(kinds).
// Stable statement time lets PostgreSQL use the due-time index range; writes
// still check/extend ownership against wall-clock time after acquiring row locks.
// The correlated LIMIT requires ownership to be evaluated before either branch
// locks candidate jobs; an uncorrelated CROSS JOIN alone permits join reordering.
const claimJobsSQL = goJobOwnershipCTE + `, kinds AS (SELECT DISTINCT unnest($1::text[]) AS kind),
 due AS MATERIALIZED (
 SELECT item.id,item.available_at AS due_at FROM ownership CROSS JOIN kinds k CROSS JOIN LATERAL (
   SELECT id,available_at FROM public.assozeta_go_jobs WHERE kind=k.kind
   AND state='pending' AND available_at<=statement_timestamp()
   ORDER BY available_at,id LIMIT CASE WHEN ownership.generation>0 THEN $2 ELSE 0 END FOR UPDATE SKIP LOCKED
 ) item
 UNION ALL
 SELECT item.id,item.lease_until AS due_at FROM ownership CROSS JOIN kinds k CROSS JOIN LATERAL (
   SELECT id,lease_until FROM public.assozeta_go_jobs WHERE kind=k.kind
   AND state='running' AND lease_until<=statement_timestamp()
   ORDER BY lease_until,id LIMIT CASE WHEN ownership.generation>0 THEN $2 ELSE 0 END FOR UPDATE SKIP LOCKED
 ) item
 ), candidates AS (
 SELECT id FROM due ORDER BY due_at,id LIMIT $2
 ), updated AS (
 UPDATE public.assozeta_go_jobs j SET
 state=CASE WHEN attempts>=max_attempts THEN 'failed' ELSE 'running' END,
 attempts=CASE WHEN attempts>=max_attempts THEN attempts ELSE attempts+1 END,
 lease_owner=CASE WHEN attempts>=max_attempts THEN '' ELSE $3 END,
 lease_token=CASE WHEN attempts>=max_attempts THEN '00000000-0000-0000-0000-000000000000'::uuid ELSE $4::uuid END,
 lease_until=CASE WHEN attempts>=max_attempts THEN NULL ELSE clock_timestamp()+make_interval(secs=>$5::double precision/1000) END,
 ownership_generation=o.generation,
 finished_at=CASE WHEN attempts>=max_attempts THEN clock_timestamp() ELSE NULL END,
 error_code=CASE WHEN attempts>=max_attempts THEN 'lease_expired' ELSE error_code END
 FROM candidates c CROSS JOIN ownership o WHERE j.id=c.id RETURNING j.*)
 SELECT ` + jobColumns + ` FROM updated WHERE state='running'`
