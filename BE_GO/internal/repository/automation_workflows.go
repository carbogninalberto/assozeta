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

func (r *AutomationWorkflows) BeginAutomationWorkflowMutations(ctx context.Context) (domain.AutomationWorkflowTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &automationWorkflowTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}, nil
}

// Reuse the shared owner/association lock and owned transaction.
type automationWorkflowTransaction struct{ *paymentCategoryTransaction }

var _ domain.AutomationWorkflowTransaction = (*automationWorkflowTransaction)(nil)

// UniqueValidator checks every association, like the primary-key queryset.
func (t *automationWorkflowTransaction) AutomationWorkflowIDExists(ctx context.Context, id string) (bool, error) {
	var exists bool
	err := t.tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM communications_automationworkflow WHERE automation_workflow_id=$1)`, id)
	return exists, err
}

func (t *automationWorkflowTransaction) InsertAutomationWorkflow(ctx context.Context, row domain.AutomationWorkflowRecord) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO communications_automationworkflow
(automation_workflow_id,sport_association_id,name,description,automation_tree,enabled,created_at,updated_at)
VALUES($1,$2,$3,$4,$5::jsonb,$6,$7,$8)`, row.ID, row.AssociationID, row.Name, row.Description, string(row.Tree), row.Enabled,
		row.CreatedAt, row.UpdatedAt)
	return err
}

func (t *automationWorkflowTransaction) AutomationWorkflowForUpdate(ctx context.Context, id, association string) (domain.AutomationWorkflowRecord, error) {
	var row domain.AutomationWorkflowRecord
	err := t.tx.GetContext(ctx, &row, automationWorkflowColumns+` WHERE automation_workflow_id=$1 AND sport_association_id=$2 FOR UPDATE`, id, association)
	if errors.Is(err, sql.ErrNoRows) {
		err = domain.ErrAutomationWorkflowNotFound
	}
	return row, err
}

func (t *automationWorkflowTransaction) DeleteAutomationWorkflow(ctx context.Context, id, association string) error {
	result, err := t.tx.ExecContext(ctx, `DELETE FROM communications_automationworkflow WHERE automation_workflow_id=$1 AND sport_association_id=$2`, id, association)
	if err != nil {
		return err
	}
	if count, err := result.RowsAffected(); err != nil || count != 1 {
		return errors.Join(err, domain.ErrAutomationWorkflowNotFound)
	}
	return nil
}

func (t *automationWorkflowTransaction) AppendAutomationWorkflowAudit(ctx context.Context, event domain.AutomationWorkflowAudit) error {
	// The registered direct resolver indexes a live row; after a delete the
	// row is gone and resolution falls back to the changes JSON.
	path := "direct"
	if event.Action == 2 {
		path = "changes_json.sport_association"
	}
	return appendModelAuditForApp(ctx, t.tx, "communications", modelAuditEntry{Model: "automationworkflow", Key: event.ID,
		Representation: "AutomationWorkflow object (" + event.ID + ")", Action: event.Action, Changes: event.Changes,
		Context: event.Context, AssociationID: &event.AssociationID, ResolutionPath: &path, At: event.At})
}
