package repository

import (
	"context"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jmoiron/sqlx"
)

// The candidate query already holds the authority and job locks. Adopt any
// pre-ledger row, retire its expired lease, then use the shared attempt as the
// new lease token. A late completion can only address its own attempt.
func registerDurableClaim(ctx context.Context, tx *sqlx.Tx, job *domain.Job, consumer string) error {
	taskID := job.ID.String()
	_, err := tx.ExecContext(ctx, `INSERT INTO public.assozeta_handoff_work
 (task_id,task_name,transport,origin_runtime,owner_runtime,owner_generation,state,accepted_at,eta,association_id)
 VALUES ($1,$2,'sql','go','go',$3,'pending',$4,$5,$6) ON CONFLICT (task_id) DO NOTHING`,
		taskID, job.Kind, job.OwnershipGeneration, job.CreatedAt, job.AvailableAt, job.AssociationID)
	if err != nil {
		return err
	}
	released, err := tx.ExecContext(ctx, `WITH released AS (
 UPDATE public.assozeta_handoff_attempts SET outcome='released',finished_at=clock_timestamp()
 WHERE task_id=$1 AND runtime='go' AND outcome='running' RETURNING attempt_id
)
UPDATE public.assozeta_handoff_effects e SET state='ambiguous' FROM released r
WHERE e.attempt_id=r.attempt_id AND e.state='intent'`, taskID)
	if err != nil {
		return err
	}
	count, err := released.RowsAffected()
	if err != nil {
		return err
	}
	if count > 0 {
		job.State = "pending"
		if _, err = tx.ExecContext(ctx, `UPDATE public.assozeta_handoff_work
 SET state='blocked',block_reason='ambiguous_effect',updated_at=clock_timestamp() WHERE task_id=$1`, taskID); err != nil {
			return err
		}
		_, err = tx.ExecContext(ctx, `UPDATE public.assozeta_go_jobs
 SET state='pending',attempts=greatest(attempts-1,0),finished_at=NULL,error_code='',`+clearJobLease+` WHERE id=$1`, job.ID)
		return err
	}
	state, retries := "pending", job.Attempts-1
	if job.State == "failed" {
		state, retries = "failed", job.Attempts
	}
	_, err = tx.ExecContext(ctx, `UPDATE public.assozeta_handoff_work SET state=$2,retries=$3,updated_at=clock_timestamp()
 WHERE task_id=$1`, taskID, state, retries)
	if err != nil || job.State == "failed" {
		return err
	}
	var attempt uuid.UUID
	err = tx.QueryRowxContext(ctx, `SELECT c.attempt_id,coalesce(nullif(w.root_id,''),w.task_id)
 FROM public.assozeta_handoff_claim('go',$1,$2,$3,NULL) c
 CROSS JOIN public.assozeta_handoff_work w WHERE c.outcome='claimed' AND w.task_id=$2`,
		consumer, taskID, job.Kind).Scan(&attempt, &job.HandoffRootID)
	if err != nil {
		return err
	}
	job.LeaseToken = attempt
	_, err = tx.ExecContext(ctx, `UPDATE public.assozeta_go_jobs SET lease_token=$2 WHERE id=$1`, job.ID, attempt)
	return err
}

func finishDurableAttempt(ctx context.Context, tx *sqlx.Tx, token uuid.UUID, outcome string) error {
	installed, err := handoffInstalled(ctx, tx)
	if err != nil || !installed {
		return err
	}
	_, err = tx.ExecContext(ctx, `SELECT public.assozeta_handoff_finish($1,$2)`, token, outcome)
	var pgError *pgconn.PgError
	if errors.As(err, &pgError) && pgError.Code == "AZF01" {
		return domain.ErrJobLeaseLost
	}
	return err
}

func (r *Jobs) RecordFenced(ctx context.Context, attempt uuid.UUID) error {
	installed, err := handoffInstalled(ctx, r.db)
	if err != nil || !installed {
		return err
	}
	_, err = r.db.ExecContext(ctx, `SELECT public.assozeta_handoff_record_fenced($1)`, attempt)
	return err
}

// HandoffHeartbeat returns the freeze this consumer has just acknowledged.
// Claim checks the same authority row under lock for every execution slot.
func (r *Jobs) HandoffHeartbeat(ctx context.Context, consumer string) (*uuid.UUID, error) {
	installed, err := handoffInstalled(ctx, r.db)
	if err != nil || !installed {
		return nil, err
	}
	var operation *uuid.UUID
	err = r.db.QueryRowxContext(ctx, `SELECT operation_id FROM public.assozeta_handoff_heartbeat($1,'go','durable')`, consumer).Scan(&operation)
	if err != nil || operation == nil {
		return nil, err
	}
	var outcome string
	err = r.db.GetContext(ctx, &outcome, `SELECT public.assozeta_handoff_acknowledge_freeze($1,$2)`, consumer, operation)
	if err != nil || outcome != "acknowledged" {
		return nil, err
	}
	return operation, nil
}

// WithConsumerID sets the process identity shared by all of this worker's executor slots.
func (r *Jobs) WithConsumerID(id string) *Jobs {
	return &Jobs{db: r.db, consumerID: id}
}
