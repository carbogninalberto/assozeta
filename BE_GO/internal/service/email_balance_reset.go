package service

import (
	"context"
	"encoding/json"
	"log/slog"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

type EmailBalanceReset struct {
	store  domain.EmailBalanceResetStore
	logger *slog.Logger
}

func NewEmailBalanceReset(store domain.EmailBalanceResetStore, logger *slog.Logger) *EmailBalanceReset {
	return &EmailBalanceReset{store: store, logger: logger}
}

func (s *EmailBalanceReset) Run(ctx context.Context, job domain.Job) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	var payload map[string]json.RawMessage
	if job.AssociationID != uuid.Nil || job.ID == uuid.Nil || job.LeaseToken == uuid.Nil || job.State != "running" || job.Kind != domain.JobResetDailyEmailBalance || job.Version != 1 || len(job.Payload) > 65536 || json.Unmarshal(job.Payload, &payload) != nil || payload == nil || len(payload) != 0 {
		return domain.ErrJobInvalid
	}
	tx, err := s.store.BeginEmailBalanceReset(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	count, err := tx.ResetPositiveCounters(ctx)
	if err != nil {
		// The existing task catches UPDATE errors and returns None without retry.
		// Roll back the failed statement before recording that handled outcome. A
		// cancelled context or unavailable completion store must remain recoverable.
		rollbackErr := tx.Rollback()
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if rollbackErr != nil {
			return rollbackErr
		}
		s.logger.WarnContext(ctx, "daily email balance reset failed", "code", "email_balance_reset_failed")
		completion, beginErr := s.store.BeginEmailBalanceReset(ctx)
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
	s.logger.InfoContext(ctx, "daily email balance reset completed", "configurations", count)
	return nil
}
