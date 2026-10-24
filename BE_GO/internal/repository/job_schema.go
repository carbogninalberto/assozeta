package repository

import (
	"context"
	"errors"
)

// CheckSchema is read-only and precedes worker readiness. Maintenance admission
// may defer every claim, so claims alone cannot detect an absent operational
// schema. A missing ownership row remains an inert state; dirty or unknown
// history and absent tables require an explicit migration or operator repair.
func (r *Jobs) CheckSchema(ctx context.Context, currentVersion uint) error {
	var ready bool
	if err := r.db.GetContext(ctx, &ready, `SELECT count(*)=1 AND
 COALESCE(bool_and(version=$1 AND NOT dirty),false) FROM public.assozeta_go_schema_migrations`, currentVersion); err != nil {
		return err
	}
	if !ready {
		return errors.New("incompatible operational schema")
	}
	rows, err := r.db.QueryxContext(ctx, `SELECT j.id,j.ownership_generation,o.singleton,o.runtime,o.generation,
 p.id,p.periodic_task_id,p.source_name,p.dispatch_sequence,p.job_id,p.dispatched_at,p.ownership_generation,p.definition_sha256
 FROM public.assozeta_go_jobs j CROSS JOIN public.assozeta_go_job_ownership o
 CROSS JOIN public.assozeta_go_periodic_occurrences p LIMIT 0`)
	if err != nil {
		return err
	}
	return rows.Close()
}
