package service

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type AuditSummaries struct{ store domain.AuditSummaryStore }

func NewAuditSummaries(store domain.AuditSummaryStore) *AuditSummaries {
	return &AuditSummaries{store: store}
}

// Stats follows audit_log_stats; a missing association is ErrAssociationNotFound.
func (s *AuditSummaries) Stats(ctx context.Context, identity domain.ResolvedIdentity) (map[string]any, error) {
	association, err := s.store.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	total, err := s.store.IndexedCount(ctx, association)
	if err != nil {
		return nil, err
	}
	counts, err := s.store.ActionCounts(ctx, association)
	if err != nil {
		return nil, err
	}
	stats := dto.AuditStats{Total: total}
	for _, row := range counts {
		switch row.Action {
		case 0:
			stats.ByAction.Create = row.Count
		case 1:
			stats.ByAction.Update = row.Count
		case 2:
			stats.ByAction.Delete = row.Count
		}
	}
	return map[string]any{"data": stats}, nil
}

// Models follows audit_log_models.
func (s *AuditSummaries) Models(ctx context.Context, identity domain.ResolvedIdentity) (map[string]any, error) {
	association, err := s.store.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	names, err := s.store.IndexedModels(ctx, association)
	if err != nil {
		return nil, err
	}
	return map[string]any{"data": dto.AuditModelsFrom(names)}, nil
}
