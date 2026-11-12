package dto

import (
	"encoding/json"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// AutomationWorkflowRow is AutomationWorkflowSerializer(fields='__all__').
type AutomationWorkflowRow struct {
	ID            string          `json:"automation_workflow_id"`
	Name          string          `json:"name"`
	Description   *string         `json:"description"`
	Tree          json.RawMessage `json:"automation_tree"`
	Enabled       bool            `json:"enabled"`
	CreatedAt     *string         `json:"created_at"`
	UpdatedAt     *string         `json:"updated_at"`
	AssociationID string          `json:"sport_association"`
}

func AutomationWorkflowRowFrom(record domain.AutomationWorkflowRecord) AutomationWorkflowRow {
	row := AutomationWorkflowRow{ID: record.ID, Name: record.Name, Description: record.Description, Tree: record.Tree,
		Enabled: record.Enabled, AssociationID: record.AssociationID}
	if record.CreatedAt != nil {
		value := paymentCategoryResponseTime(*record.CreatedAt)
		row.CreatedAt = &value
	}
	if record.UpdatedAt != nil {
		value := paymentCategoryResponseTime(*record.UpdatedAt)
		row.UpdatedAt = &value
	}
	return row
}
