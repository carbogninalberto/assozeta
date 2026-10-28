package domain

import "context"

type AuditActionCount struct {
	Action int   `db:"action"`
	Count  int64 `db:"count"`
}

type AuditSummaryStore interface {
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	// IndexedCount counts the association's index rows.
	IndexedCount(ctx context.Context, association string) (int64, error)
	ActionCounts(ctx context.Context, association string) ([]AuditActionCount, error)
	// IndexedModels lists the distinct content type models of indexed entries.
	IndexedModels(ctx context.Context, association string) ([]string, error)
}
