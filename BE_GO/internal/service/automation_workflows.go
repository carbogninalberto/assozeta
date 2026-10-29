package service

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type AutomationWorkflowReads struct {
	store domain.AutomationWorkflowReadStore
}

func NewAutomationWorkflowReads(store domain.AutomationWorkflowReadStore) *AutomationWorkflowReads {
	return &AutomationWorkflowReads{store: store}
}

// Admit is IsAssociationUser; it precedes OPTIONS and method dispatch.
func (s *AutomationWorkflowReads) Admit(ctx context.Context, identity domain.ResolvedIdentity) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	_, err := messageReadsOwner(identity)
	return err
}

func (s *AutomationWorkflowReads) List(ctx context.Context, identity domain.ResolvedIdentity) ([]dto.AutomationWorkflowRow, error) {
	owner, err := messageReadsOwner(identity)
	if err != nil {
		return nil, err
	}
	records, err := s.store.AutomationWorkflows(ctx, owner)
	if err != nil {
		return nil, err
	}
	rows := make([]dto.AutomationWorkflowRow, 0, len(records))
	for _, record := range records {
		rows = append(rows, dto.AutomationWorkflowRowFrom(record))
	}
	return rows, nil
}

// Details validates the URL UUID before dereferencing the association.
func (s *AutomationWorkflowReads) Details(ctx context.Context, identity domain.ResolvedIdentity, raw string) (dto.AutomationWorkflowRow, error) {
	owner, err := messageReadsOwner(identity)
	if err != nil {
		return dto.AutomationWorkflowRow{}, err
	}
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return dto.AutomationWorkflowRow{}, err
	}
	record, err := s.store.AutomationWorkflow(ctx, owner, id)
	if err != nil {
		return dto.AutomationWorkflowRow{}, err
	}
	return dto.AutomationWorkflowRowFrom(record), nil
}
