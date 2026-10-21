package repository

import (
	"context"
	"io"
	"time"

	"github.com/jmoiron/sqlx"
)

type RestoreOperationLocker interface {
	Acquire(context.Context, bool, bool) (io.Closer, error)
}

type RestoreStateReader interface {
	Pending(context.Context) (bool, error)
}

type JobRestoreStateReader interface {
	JobBarrier(context.Context) (bool, *time.Time, error)
}

// Read under the shared file lease: a completed replacement cannot race this
// snapshot. Pending receipts and the latest completion are instance-wide.
func (r *RestoreStates) JobBarrier(ctx context.Context) (bool, *time.Time, error) {
	var state struct {
		Pending   bool       `db:"pending"`
		Completed *time.Time `db:"completed"`
	}
	err := r.db.GetContext(ctx, &state, `SELECT
 EXISTS(SELECT 1 FROM instance_datarestore WHERE state IN ($1,$2)) AS pending,
 (SELECT completed_at FROM instance_datarestore WHERE state=$3 AND completed_at IS NOT NULL ORDER BY completed_at DESC LIMIT 1) AS completed`, "queued", "running", "completed")
	return state.Pending, state.Completed, err
}

type RestoreStates struct{ db *sqlx.DB }

func NewRestoreStates(db *sqlx.DB) *RestoreStates { return &RestoreStates{db: db} }

// Restore replaces instance-wide state. This query deliberately spans every
// association, matching DataRestore.ACTIVE rather than an authenticated scope.
func (r *RestoreStates) Pending(ctx context.Context) (bool, error) {
	var pending bool
	err := r.db.GetContext(ctx, &pending, `SELECT EXISTS (SELECT 1 FROM instance_datarestore WHERE state IN ($1, $2))`, "queued", "running")
	return pending, err
}
