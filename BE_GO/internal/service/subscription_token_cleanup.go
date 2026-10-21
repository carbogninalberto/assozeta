package service

import (
	"context"
	"encoding/json"
	"log/slog"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

type SubscriptionTokenCleanup struct {
	store  domain.SubscriptionTokenCleanupStore
	now    func() time.Time
	logger *slog.Logger
}

func NewSubscriptionTokenCleanup(store domain.SubscriptionTokenCleanupStore, now func() time.Time, logger *slog.Logger) *SubscriptionTokenCleanup {
	return &SubscriptionTokenCleanup{store: store, now: now, logger: logger}
}
func (s *SubscriptionTokenCleanup) Run(ctx context.Context, job domain.Job) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	var payload map[string]json.RawMessage
	if job.AssociationID != uuid.Nil || job.ID == uuid.Nil || job.LeaseToken == uuid.Nil || job.State != "running" || job.Kind != domain.JobClearExpiredSubscriptionTokens || job.Version != 1 || len(job.Payload) > 65536 || json.Unmarshal(job.Payload, &payload) != nil || payload == nil || len(payload) != 0 {
		return domain.ErrJobInvalid
	}
	// Django's datetime.now().date() runs with TIME_ZONE=UTC, and the ORM
	// coerces that date to midnight, not to the current instant.
	now := s.now().UTC()
	cutoff := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC)
	tx, err := s.store.BeginSubscriptionTokenCleanup(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	count, err := tx.DeleteExpiredWithAudit(ctx, cutoff, s.now().UTC())
	if err != nil {
		rollbackErr := tx.Rollback()
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if rollbackErr != nil {
			return rollbackErr
		}
		// The legacy task catches collector/delete/audit errors and returns None.
		// Preserve that handled outcome only after all business work is rolled back.
		s.logger.WarnContext(ctx, "subscription token cleanup failed", "code", "subscription_token_cleanup_failed")
		completion, beginErr := s.store.BeginSubscriptionTokenCleanup(ctx)
		if beginErr != nil {
			return beginErr
		}
		defer completion.Rollback()
		if err = completion.CompleteJob(ctx, job.ID, job.LeaseToken); err != nil {
			return err
		}
		return completion.Commit()
	}
	if err = tx.CompleteJob(ctx, job.ID, job.LeaseToken); err != nil {
		return err
	}
	if err = tx.Commit(); err != nil {
		return err
	}
	s.logger.InfoContext(ctx, "expired subscription tokens removed", "tokens", count)
	return nil
}
