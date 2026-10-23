package service

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

type AuditRetention struct {
	store  domain.AuditRetentionStore
	now    func() time.Time
	logger *slog.Logger
}

func NewAuditRetention(store domain.AuditRetentionStore, now func() time.Time, logger *slog.Logger) *AuditRetention {
	return &AuditRetention{store: store, now: now, logger: logger}
}
func (s *AuditRetention) Run(ctx context.Context, job domain.Job) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	var payload map[string]json.RawMessage
	if job.AssociationID != uuid.Nil || job.ID == uuid.Nil || job.LeaseToken == uuid.Nil || job.State != "running" || job.Kind != domain.JobDeleteOldAuditLogs || job.Version != 1 || len(job.Payload) > 65536 || json.Unmarshal(job.Payload, &payload) != nil || payload == nil || len(payload) != 0 {
		return domain.ErrJobInvalid
	}
	// The existing task uses timezone.now() - timedelta(days=365): retain
	// equality and preserve the exact instant across leap years and offsets.
	cutoff := s.now().UTC().Add(-365 * 24 * time.Hour)

	tx, err := s.store.BeginAuditRetention(ctx, job)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	count, err := tx.DeleteExpiredAuditRecords(ctx, cutoff)
	if err != nil {
		rollbackErr := tx.Rollback()
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if rollbackErr != nil {
			return rollbackErr
		}
		return s.completeHandledFailure(ctx, job)
	}
	if err = tx.CompleteJob(ctx, job.ID, job.LeaseToken); err != nil {
		return err
	}
	if err = tx.Commit(); err != nil {
		if errors.Is(err, domain.ErrAuditRetentionConflict) {
			return s.completeHandledFailure(ctx, job)
		}
		return err
	}
	s.logger.InfoContext(ctx, "expired audit records removed", "records", count)
	return nil
}

// Legacy collector errors, including the definite deferred-FK rollback, return
// None. Complete that handled outcome only after the business transaction ended.
func (s *AuditRetention) completeHandledFailure(ctx context.Context, job domain.Job) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	s.logger.WarnContext(ctx, "audit retention failed", "code", "audit_retention_failed")
	completion, err := s.store.BeginAuditRetention(ctx, job)
	if err != nil {
		return err
	}
	defer completion.Rollback()
	if err = completion.CompleteJob(ctx, job.ID, job.LeaseToken); err != nil {
		return err
	}
	return completion.Commit()
}
