package service

import (
	"context"
	"encoding/json"
	"errors"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
)

// ErrAutomationWorkflowMissing reproduces the view dereferencing a missing row
// (AttributeError, HTTP 500) on delete; it is not mapped to 404.
var ErrAutomationWorkflowMissing = errors.New("workflow delete target does not exist")

type AutomationWorkflowWrites struct {
	store domain.AutomationWorkflowStore
	now   func() time.Time
}

func NewAutomationWorkflowWrites(store domain.AutomationWorkflowStore, now func() time.Time) *AutomationWorkflowWrites {
	return &AutomationWorkflowWrites{store: store, now: now}
}

func (s *AutomationWorkflowWrites) Admit(ctx context.Context, identity domain.ResolvedIdentity) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	_, err := messageReadsOwner(identity)
	return err
}

func (s *AutomationWorkflowWrites) begin(ctx context.Context, identity domain.ResolvedIdentity) (domain.AutomationWorkflowTransaction, string, error) {
	owner, err := paymentCategoryOwner(identity, domain.ErrMessageReadForbidden)
	if err != nil {
		return nil, "", err
	}
	tx, err := s.store.BeginAutomationWorkflowMutations(ctx)
	if err != nil {
		return nil, "", err
	}
	role, active, deleted, err := tx.OwnerForUpdate(ctx, owner)
	if err == nil && (role != domain.RoleAssociation || !active || deleted) {
		err = domain.ErrMessageReadForbidden
	}
	var association string
	if err == nil {
		association, err = tx.AssociationForOwner(ctx, owner, paymentCategoryAssociationScope(identity))
	}
	if err != nil {
		_ = tx.Rollback()
		return nil, "", err
	}
	return tx, association, nil
}

// automationWorkflowChanges renders auditlog's string pairs: JSON as sorted
// ASCII json.dumps, datetimes as naive UTC str(), None values omitted.
func automationWorkflowChanges(row domain.AutomationWorkflowRecord, action int) (map[string][2]string, error) {
	tree, err := dto.ParseProfileTablesJSON(row.Tree)
	if err != nil {
		return nil, err
	}
	values := map[string]string{"automation_workflow_id": row.ID, "sport_association": row.AssociationID, "name": row.Name,
		"automation_tree": string(dto.PythonJSONDumpsSorted(tree)), "enabled": strconv.FormatBool(row.Enabled)}
	values["enabled"] = map[bool]string{true: "True", false: "False"}[row.Enabled]
	if row.Description != nil {
		values["description"] = *row.Description
	}
	if row.CreatedAt != nil {
		values["created_at"] = paymentCategoryAuditTime(*row.CreatedAt)
	}
	if row.UpdatedAt != nil {
		values["updated_at"] = paymentCategoryAuditTime(*row.UpdatedAt)
	}
	changes := make(map[string][2]string, len(values))
	for key, value := range values {
		if action == 0 {
			changes[key] = [2]string{"None", value}
		} else {
			changes[key] = [2]string{value, "None"}
		}
	}
	return changes, nil
}

func (s *AutomationWorkflowWrites) Add(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool, audit domain.LoginAuditContext) (string, error) {
	if err := ctx.Err(); err != nil {
		return "", err
	}
	if _, err := messageReadsOwner(identity); err != nil {
		return "", err
	}
	input, err := dto.ParseAutomationWorkflowWrite(body, form)
	if errors.Is(err, dto.ErrAutomationWorkflowBody) {
		return "", err
	}
	tx, association, beginErr := s.begin(ctx, identity)
	if beginErr != nil {
		return "", beginErr
	}
	defer tx.Rollback()
	id := uuid.NewString()
	if err == nil && input.ID != nil {
		id = *input.ID
		if exists, existsErr := tx.AutomationWorkflowIDExists(ctx, id); existsErr != nil {
			return "", existsErr
		} else if exists {
			err = dto.ErrAutomationWorkflowInvalid
		}
	}
	if err != nil {
		return "", err
	}
	at := s.now().UTC().Truncate(time.Microsecond)
	row := domain.AutomationWorkflowRecord{ID: id, AssociationID: association, Name: input.Name, Description: input.Description,
		Tree: json.RawMessage(dto.PythonJSONDumps(input.Tree)), Enabled: input.Enabled, CreatedAt: &at, UpdatedAt: &at}
	if err = tx.InsertAutomationWorkflow(ctx, row); err != nil {
		return "", err
	}
	changes, err := automationWorkflowChanges(row, 0)
	if err != nil {
		return "", err
	}
	if err = tx.AppendAutomationWorkflowAudit(ctx, domain.AutomationWorkflowAudit{ID: id, AssociationID: association, Action: 0,
		Changes: changes, Context: paymentCategoryAuditContext(identity, audit), At: at}); err != nil {
		return "", err
	}
	if err = ctx.Err(); err != nil {
		return "", err
	}
	return id, tx.Commit()
}

func (s *AutomationWorkflowWrites) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if _, err := messageReadsOwner(identity); err != nil {
		return err
	}
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return err
	}
	tx, association, err := s.begin(ctx, identity)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	row, err := tx.AutomationWorkflowForUpdate(ctx, id, association)
	if errors.Is(err, domain.ErrAutomationWorkflowNotFound) {
		return ErrAutomationWorkflowMissing
	}
	if err != nil {
		return err
	}
	if err = tx.DeleteAutomationWorkflow(ctx, id, association); err != nil {
		return err
	}
	changes, err := automationWorkflowChanges(row, 2)
	if err != nil {
		return err
	}
	if err = tx.AppendAutomationWorkflowAudit(ctx, domain.AutomationWorkflowAudit{ID: id, AssociationID: association, Action: 2,
		Changes: changes, Context: paymentCategoryAuditContext(identity, audit), At: s.now().UTC().Truncate(time.Microsecond)}); err != nil {
		return err
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	return tx.Commit()
}
