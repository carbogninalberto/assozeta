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

// SubscriptionAutoArchive is auto_archive_subscription: every live,
// unarchived subscription whose association owner enables auto_archive is
// archived, with its payment and that payment's invoice, once the balance
// year containing its creation date has ended (the instant is frozen at the
// start of the run, like timezone.now()). Each subscription is its own atomic
// block; a failure is logged and the run goes on.
type SubscriptionAutoArchive struct {
	store  domain.SubscriptionAutoArchiveStore
	now    func() time.Time
	logger *slog.Logger
}

func NewSubscriptionAutoArchive(store domain.SubscriptionAutoArchiveStore, now func() time.Time, logger *slog.Logger) *SubscriptionAutoArchive {
	return &SubscriptionAutoArchive{store: store, now: now, logger: logger}
}

func (s *SubscriptionAutoArchive) Run(ctx context.Context, job domain.Job) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	var payload map[string]json.RawMessage
	if job.AssociationID != uuid.Nil || job.ID == uuid.Nil || job.LeaseToken == uuid.Nil || job.State != "running" ||
		job.Kind != domain.JobAutoArchiveSubscription || job.Version != 1 || len(job.Payload) > 65536 ||
		json.Unmarshal(job.Payload, &payload) != nil || payload == nil || len(payload) != 0 {
		return domain.ErrJobInvalid
	}
	now := s.now().UTC()
	candidates, err := s.candidates(ctx, job)
	if err != nil {
		return err
	}
	archived := 0
	for _, candidate := range candidates {
		if err := ctx.Err(); err != nil {
			return err
		}
		// TIME_ZONE is UTC, so timezone.localtime(creation_date) is the UTC
		// instant; year_end is the start of the balance year's last day.
		period, err := SeasonRange(ctx, candidate.Created, candidate.Fiscal)
		if err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			s.logger.WarnContext(ctx, "auto_archive_subscription: could not archive subscription", "code", "subscription_archive_failed",
				"subscription_id", candidate.ID.String())
			continue
		}
		if now.Before(period.End.Add(24 * time.Hour)) {
			continue
		}
		done, err := s.archive(ctx, job, candidate)
		if err != nil {
			return err
		}
		if done {
			archived++
		}
	}
	tx, err := s.store.BeginSubscriptionArchive(ctx, job)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = tx.CompleteJob(ctx, job.ID, job.LeaseToken); err != nil {
		return err
	}
	if err = tx.Commit(); err != nil {
		return err
	}
	s.logger.InfoContext(ctx, "auto_archive_subscription: archived subscriptions", "subscriptions", archived)
	return nil
}

func (s *SubscriptionAutoArchive) candidates(ctx context.Context, job domain.Job) ([]domain.SubscriptionArchiveCandidate, error) {
	tx, err := s.store.BeginSubscriptionArchive(ctx, job)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	candidates, err := tx.ArchiveCandidates(ctx)
	if err != nil {
		return nil, err
	}
	return candidates, tx.Commit()
}

// archive runs one atomic block. Subscription.save validates the owner's
// subscription calendar before writing, so an invalid calendar fails the
// block before any effect; any other failure rolls the block back. Both are
// logged like Django's handler. Lease loss, cancellation and commit failures
// end the job.
func (s *SubscriptionAutoArchive) archive(ctx context.Context, job domain.Job, candidate domain.SubscriptionArchiveCandidate) (bool, error) {
	failed := func(err error) (bool, error) {
		if ctx.Err() != nil {
			return false, ctx.Err()
		}
		if errors.Is(err, domain.ErrJobLeaseLost) {
			return false, err
		}
		s.logger.WarnContext(ctx, "auto_archive_subscription: could not archive subscription", "code", "subscription_archive_failed",
			"subscription_id", candidate.ID.String())
		return false, nil
	}
	if _, err := ResolveSubscriptionDates(ctx, candidate.Created, candidate.Rule, candidate.Dates); err != nil {
		return failed(err)
	}
	tx, err := s.store.BeginSubscriptionArchive(ctx, job)
	if err != nil {
		return false, err
	}
	defer tx.Rollback()
	if err = tx.ArchiveSubscription(ctx, candidate); err != nil {
		if rollbackErr := tx.Rollback(); rollbackErr != nil && ctx.Err() == nil {
			return false, rollbackErr
		}
		return failed(err)
	}
	return true, tx.Commit()
}
