package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// PromoteRenewalDelivery moves a due broker delivery to the native queue.
// The broker is acknowledged only after this transaction commits. Replaying
// an unacknowledged copy observes the native or terminal ledger row and is safe.
func (r *Jobs) PromoteRenewalDelivery(ctx context.Context, message *celery.Message, payload json.RawMessage) error {
	id, err := uuid.Parse(message.ID)
	if err != nil || id == uuid.Nil || message.Task != domain.JobRenewMembershipsPayments {
		return domain.ErrJobInvalid
	}
	return r.withOwnedTransaction(ctx, func(tx *sqlx.Tx) error {
		var generation int64
		err := tx.GetContext(ctx, &generation, `SELECT generation FROM public.assozeta_go_job_ownership
 WHERE singleton=1 AND runtime='go' AND admission='open' FOR SHARE`)
		if errors.Is(err, sql.ErrNoRows) {
			return domain.ErrHandoffAdmissionRejected
		}
		if err != nil {
			return err
		}
		var exists bool
		if err = tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM public.assozeta_handoff_work WHERE task_id=$1)`, message.ID); err != nil {
			return err
		}
		if !exists {
			// Recover a pre-ledger delivery on the Go queue without assigning a
			// new task ID or replacing the original publication timestamp.
			stamp := time.Unix(0, 0)
			if message.PublishedAt != nil {
				stamp = time.Unix(0, int64(*message.PublishedAt*1e9))
			}
			var outcome string
			if err = tx.GetContext(ctx, &outcome, `SELECT outcome FROM public.assozeta_handoff_admit(
 'go',$1,$2,'broker',$3,$4,$5,$6,$7)`, message.ID, message.Task, celery.GoQueue, message.Raw, stamp, message.ETA, message.Expires); err != nil {
				return err
			}
			if outcome != "accepted" && outcome != "duplicate" {
				return domain.ErrHandoffAdmissionRejected
			}
		}
		var recorded bool
		err = tx.GetContext(ctx, &recorded, `WITH pending AS MATERIALIZED (
 SELECT task_id,task_name,association_id,retries,accepted_at,eta FROM public.assozeta_handoff_work
 WHERE task_id=$1 AND task_name=$2 AND owner_runtime='go' AND owner_generation=$3 AND retries=$6
 AND transport='broker' AND state='pending' AND (eta IS NULL OR eta<=clock_timestamp()) FOR UPDATE
), queued AS (
 INSERT INTO public.assozeta_go_jobs (id,association_id,kind,version,idempotency_key,payload,
 max_attempts,attempts,available_at,created_at)
 SELECT $4,coalesce(association_id,'00000000-0000-0000-0000-000000000000'),task_name,
 1,'handoff:'||task_id,$5::jsonb,greatest(3,retries+1),retries,coalesce(eta,accepted_at),accepted_at FROM pending
 ON CONFLICT (id) DO UPDATE SET state='pending',error_code='',finished_at=NULL,payload=excluded.payload,
 attempts=excluded.attempts,available_at=excluded.available_at
 WHERE assozeta_go_jobs.state='cancelled' AND assozeta_go_jobs.error_code='handoff_transferred'
 RETURNING id
), promoted AS (
 UPDATE public.assozeta_handoff_work SET transport='sql',queue=NULL,envelope=NULL,updated_at=clock_timestamp()
 FROM queued WHERE task_id=$1 RETURNING task_id
)
SELECT EXISTS(SELECT 1 FROM promoted) OR EXISTS(SELECT 1 FROM public.assozeta_handoff_work
 WHERE task_id=$1 AND task_name=$2 AND (transport='sql' OR retries>$6 OR
 state IN ('completed','failed','cancelled','expired','revoked','blocked')))`, message.ID, message.Task, generation, id, string(payload), message.Retries)
		if err == nil && !recorded {
			return domain.ErrHandoffAdmissionRejected
		}
		return err
	})
}
