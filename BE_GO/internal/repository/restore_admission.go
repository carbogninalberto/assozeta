package repository

import (
	"context"
	"io"

	"github.com/jmoiron/sqlx"
)

type RestoreOperationLocker interface {
	Acquire(context.Context, bool, bool) (io.Closer, error)
}

type RestoreStateReader interface {
	Pending(context.Context) (bool, error)
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
