package service

import (
	"context"
	"encoding/json"
	"log/slog"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

type AssociateAgeUpdate struct {
	store  domain.AssociateAgeUpdateStore
	now    func() time.Time
	logger *slog.Logger
}

func NewAssociateAgeUpdate(store domain.AssociateAgeUpdateStore, now func() time.Time, logger *slog.Logger) *AssociateAgeUpdate {
	return &AssociateAgeUpdate{store: store, now: now, logger: logger}
}
func (s *AssociateAgeUpdate) Run(ctx context.Context, job domain.Job) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	var payload map[string]json.RawMessage
	if job.AssociationID != uuid.Nil || job.ID == uuid.Nil || job.LeaseToken == uuid.Nil || job.State != "running" || job.Kind != domain.JobUpdateAssociateAge || job.Version != 1 || len(job.Payload) > 65536 || json.Unmarshal(job.Payload, &payload) != nil || payload == nil || len(payload) != 0 {
		return domain.ErrJobInvalid
	}
	// Legacy calculate_age truncates elapsed days / 365.25. Reaching 18
	// therefore requires 6,575 days, rather than a calendar birthday comparison.
	now := s.now().UTC()
	cutoff := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC).AddDate(0, 0, -6575)

	tx, err := s.store.BeginAssociateAgeUpdate(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	count, err := tx.PromoteAdultsWithAudit(ctx, cutoff, s.now().UTC())
	if err != nil {
		rollbackErr := tx.Rollback()
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if rollbackErr != nil {
			return rollbackErr
		}
		// The legacy task catches save/audit errors and returns None.
		// Preserve that handled outcome only after all business work is rolled back.
		s.logger.WarnContext(ctx, "associate age update failed", "code", "associate_age_update_failed")
		completion, beginErr := s.store.BeginAssociateAgeUpdate(ctx)
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
	s.logger.InfoContext(ctx, "associate ages updated", "associates", count)
	return nil
}
