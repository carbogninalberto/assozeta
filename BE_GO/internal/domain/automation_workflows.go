package domain

import (
	"context"
	"encoding/json"
	"errors"
	"time"
)

var ErrAutomationWorkflowNotFound = errors.New("workflow not found.")

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
