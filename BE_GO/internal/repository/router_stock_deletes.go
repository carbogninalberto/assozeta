package repository

import (
	"context"
	"database/sql"
	"fmt"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// RouterStockDeletes runs the Collector port (collaborator_delete_collector.go)
// for one row of any model in the generated graph.
type RouterStockDeletes struct {
	db  *sqlx.DB
	now func() time.Time
}

func NewRouterStockDeletes(db *sqlx.DB, now func() time.Time) *RouterStockDeletes {
	return &RouterStockDeletes{db: db, now: now}
}

var _ domain.RouterStockDeleteStore = (*RouterStockDeletes)(nil)

func (r *RouterStockDeletes) DeleteModel(ctx context.Context, label, key string, audit domain.LoginAuditContext, group *string) error {
	model := collaboratorDeleteGraphLoaded.Models[label]
	if model == nil {
		return fmt.Errorf("%w: %s is not in the model graph", ErrCollaboratorDeleteUnsupported, label)
	}
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return err
	}
	defer tx.Rollback()
	run := &collaboratorDeleteRun{tx: tx.tx, graph: collaboratorDeleteGraphLoaded, audit: audit, group: group, now: r.now}
	rows, err := run.load(ctx, model, `t.`+model.PK+`::text = $1 FOR UPDATE`, key)
	if err != nil {
		return err
	}
	if err = run.Delete(ctx, label, rows); err != nil {
		return err
	}
	return tx.Commit()
}
