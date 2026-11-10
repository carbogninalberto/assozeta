package domain

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

var ErrAutomationWorkflowNotFound = errors.New("workflow not found.")

// ErrAutomationWorkflowUpdateNotPorted refuses, before any write, an update whose
// Django behaviour is not ported: a cron trigger enabled for a future or
// unclassified time (Celery check_workflows_trigger_cron) or a changed key.
var ErrAutomationWorkflowUpdateNotPorted = refusal.New("workflow update not ported")

// ErrAutomationWorkflowTrigger reproduces trigger() raising after the update
// was saved (HTTP 500; the saves stay, requests are not atomic).
var ErrAutomationWorkflowTrigger = errors.New("workflow trigger failed after save")

type AutomationWorkflowRecord struct {
	ID            string          `db:"automation_workflow_id"`
	AssociationID string          `db:"sport_association_id"`
	Name          string          `db:"name"`
	Description   *string         `db:"description"`
	Tree          json.RawMessage `db:"automation_tree"`
	Enabled       bool            `db:"enabled"`
	CreatedAt     *time.Time      `db:"created_at"`
	UpdatedAt     *time.Time      `db:"updated_at"`
}

type AutomationWorkflowReadStore interface {
	AutomationWorkflows(ctx context.Context, owner string) ([]AutomationWorkflowRecord, error)
	AutomationWorkflow(ctx context.Context, owner, id string) (AutomationWorkflowRecord, error)
}

type AutomationWorkflowAudit struct {
	ID, AssociationID string
	Action            int
	Changes           map[string][2]string
	Context           LoginAuditContext
	At                time.Time
}

type AutomationWorkflowStore interface {
	BeginAutomationWorkflowMutations(context.Context) (AutomationWorkflowTransaction, error)
}

type AutomationWorkflowTransaction interface {
	OwnerForUpdate(context.Context, string) (int, bool, bool, error)
	AssociationForOwner(context.Context, string, *string) (string, error)
	AutomationWorkflowIDExists(ctx context.Context, id string) (bool, error)
	InsertAutomationWorkflow(context.Context, AutomationWorkflowRecord) error
	AutomationWorkflowForUpdate(ctx context.Context, id, association string) (AutomationWorkflowRecord, error)
	DeleteAutomationWorkflow(ctx context.Context, id, association string) error
	// AssociationExists checks any stored association (the relation's default manager).
	AssociationExists(ctx context.Context, id string) (bool, error)
	UpdateAutomationWorkflow(context.Context, AutomationWorkflowRecord) error
	AppendAutomationWorkflowAudit(context.Context, AutomationWorkflowAudit) error
	Commit() error
	Rollback() error
}
