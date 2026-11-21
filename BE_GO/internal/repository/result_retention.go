package repository

import (
	"context"
	"encoding/json"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// RunResultRetention implements django_celery_results.DatabaseBackend.cleanup.
// The cutoff uses elapsed real time. The scheduler clock only decides when to
// enqueue the occurrence. Deletion and fenced completion commit together.
func (r *Jobs) RunResultRetention(ctx context.Context, job domain.Job) error {
	var args map[string]json.RawMessage
	if job.Kind != domain.JobResultRetention || job.Version != 1 || job.State != "running" ||
		job.AssociationID != uuid.Nil || job.ID == uuid.Nil || job.LeaseToken == uuid.Nil ||
		json.Unmarshal(job.Payload, &args) != nil || args == nil || len(args) != 0 {
		return domain.ErrJobInvalid
	}
	return r.withOwnedTransaction(ctx, func(tx *sqlx.Tx) error {
		if err := fenceJobEffects(ctx, tx, job.ID, job.LeaseToken); err != nil {
			return err
		}
		_, err := tx.ExecContext(ctx, `WITH expired_tasks AS (
            DELETE FROM django_celery_results_taskresult WHERE date_done < statement_timestamp() - interval '24 hours'
        ) DELETE FROM django_celery_results_groupresult WHERE date_done < statement_timestamp() - interval '24 hours'`)
		if err != nil {
			return err
		}
		return completeJob(ctx, tx, job.ID, job.LeaseToken, nil)
	})
}
