package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type AutomationWorkflows struct{ db *sqlx.DB }

func NewAutomationWorkflows(db *sqlx.DB) *AutomationWorkflows { return &AutomationWorkflows{db: db} }

var _ domain.AutomationWorkflowReadStore = (*AutomationWorkflows)(nil)

const automationWorkflowColumns = `SELECT automation_workflow_id,sport_association_id,name,description,automation_tree,
enabled,created_at,updated_at FROM communications_automationworkflow`

// Both reads resolve the active association first (ActiveManager semantics).
func (r *AutomationWorkflows) AutomationWorkflows(ctx context.Context, owner string) ([]domain.AutomationWorkflowRecord, error) {
	association, err := messageReadsAssociation(ctx, r.db, owner)
	if err != nil {
		return nil, err
	}
	rows := []domain.AutomationWorkflowRecord{}
	err = r.db.SelectContext(ctx, &rows, automationWorkflowColumns+` WHERE sport_association_id=$1 ORDER BY created_at DESC`, association)
	return rows, err
}

func (r *AutomationWorkflows) AutomationWorkflow(ctx context.Context, owner, id string) (domain.AutomationWorkflowRecord, error) {
	var row domain.AutomationWorkflowRecord
	association, err := messageReadsAssociation(ctx, r.db, owner)
	if err != nil {
		return row, err
	}
	err = r.db.GetContext(ctx, &row, automationWorkflowColumns+` WHERE sport_association_id=$1 AND automation_workflow_id=$2`, association, id)
	if errors.Is(err, sql.ErrNoRows) {
		err = domain.ErrAutomationWorkflowNotFound
	}
	return row, err
}
