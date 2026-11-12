package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// Every claim and outcome locks this row before touching job rows. Shared locks
// permit concurrent workers and heartbeats but exclude an ownership UPDATE until
// the whole effect/completion transaction commits or rolls back. No production
// transition API exists yet: Django publication, queued tasks and scheduling must
// be coordinated before a runtime can acquire ownership.
const goJobOwnershipCTE = `WITH ownership AS MATERIALIZED (
 SELECT generation FROM public.assozeta_go_job_ownership
 WHERE singleton=1 AND runtime='go' FOR SHARE
) `

// fenceJobEffects must precede business writes in database-only task services.
// It retains the ownership lock, but never a job row lock: renewal may continue
// while the service runs. Completion still checks the live attempt/lease again.
func fenceJobEffects(ctx context.Context, tx *sqlx.Tx, id, token uuid.UUID) error {
	var generation int64
	err := tx.GetContext(ctx, &generation, goJobOwnershipCTE+`
 SELECT o.generation FROM ownership o JOIN public.assozeta_go_jobs j
 ON j.ownership_generation=o.generation
 WHERE j.id=$1 AND j.lease_token=$2 AND j.state='running' AND j.lease_until>clock_timestamp()`, id, token)
	if errors.Is(err, sql.ErrNoRows) {
		return domain.ErrJobLeaseLost
	}
	return err
}
