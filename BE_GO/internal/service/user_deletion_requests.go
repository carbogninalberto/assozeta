package service

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

// UserDeletionRequests is the delete_users_with_request task body.
type UserDeletionRequests struct {
	store     domain.UserDeletionRequestStore
	publisher celery.Publisher
	brand     string
	now       func() time.Time
	logger    *slog.Logger
}

func NewUserDeletionRequests(store domain.UserDeletionRequestStore, publisher celery.Publisher, whitelabelName string, now func() time.Time, logger *slog.Logger) *UserDeletionRequests {
	return &UserDeletionRequests{store: store, publisher: publisher, brand: whitelabelName, now: now, logger: logger}
}

// Run follows the task: the due users are read once, then for each user the
// send_email_text notice is published before that user's autocommitted
// save(). Any exception ends the task with None and keeps earlier saves; Go
// records that handled outcome as a successful NULL completion.
func (s *UserDeletionRequests) Run(ctx context.Context, job domain.Job) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	var payload map[string]json.RawMessage
	if job.AssociationID != uuid.Nil || job.ID == uuid.Nil || job.LeaseToken == uuid.Nil || job.State != "running" || job.Kind != domain.JobDeleteUsersWithRequest || job.Version != 1 || len(job.Payload) > 65536 || json.Unmarshal(job.Payload, &payload) != nil || payload == nil || len(payload) != 0 {
		return domain.ErrJobInvalid
	}
	// datetime.now().date() with TIME_ZONE=UTC.
	now := s.now().UTC()
	today := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC)
	tx, err := s.store.BeginUserDeletionRequests(ctx, job)
	if err != nil {
		return err
	}
	users, err := tx.DueUsers(ctx, today)
	if err != nil {
		_ = tx.Rollback()
		return s.handled(ctx, job, err)
	}
	if err = tx.Rollback(); err != nil {
		return err
	}
	for _, user := range users {
		if err = s.deleteOne(ctx, job, user); err != nil {
			if handled, ok := err.(handledTaskError); ok {
				return s.handled(ctx, job, handled.err)
			}
			return err
		}
	}
	completion, err := s.store.BeginUserDeletionRequests(ctx, job)
	if err != nil {
		return err
	}
	defer completion.Rollback()
	if err = completion.CompleteJob(ctx, job.ID, job.LeaseToken); err != nil {
		return err
	}
	if err = completion.Commit(); err != nil {
		return err
	}
	s.logger.InfoContext(ctx, "user deletion requests processed", "users", len(users))
	return nil
}

// handledTaskError marks a business failure the legacy task catches.
type handledTaskError struct{ err error }

func (e handledTaskError) Error() string { return e.err.Error() }

// deleteOne fences the job first, so a stale worker publishes nothing; the
// notice is published before the save, as in the task, and the save commits
// only after a successful publish.
func (s *UserDeletionRequests) deleteOne(ctx context.Context, job domain.Job, user domain.UserDeletionCandidate) error {
	tx, err := s.store.BeginUserDeletionRequests(ctx, job)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	_, err = s.publisher.Publish(ctx, celery.Task{Name: "send_email_text", Kwargs: celery.Dict{
		{Key: "subject", Value: s.brand + " | Il tuo account è stato eliminato"},
		{Key: "message", Value: "Ciao " + user.FirstName + ", Il tuo account è stato eliminato dal sistema " +
			"perché hai richiesto la cancellazione 30 giorni fa. "},
		{Key: "recipient_list", Value: []any{user.Email}},
	}})
	if err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return handledTaskError{err}
	}
	if err = tx.MarkDeleted(ctx, user, s.now().UTC()); err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if errors.Is(err, domain.ErrUserDeletionSaveFailed) {
			if commitErr := tx.Commit(); commitErr != nil {
				return commitErr
			}
		}
		return handledTaskError{err}
	}
	// A failed COMMIT is not a Django outcome (each statement autocommits): it
	// is left to the executor's retry, which reselects the still-due user.
	return tx.Commit()
}

func (s *UserDeletionRequests) handled(ctx context.Context, job domain.Job, cause error) error {
	if ctx.Err() != nil {
		return ctx.Err()
	}
	if errors.Is(cause, domain.ErrJobLeaseLost) {
		return cause
	}
	s.logger.WarnContext(ctx, "user deletion requests failed", "code", "user_deletion_requests_failed")
	completion, err := s.store.BeginUserDeletionRequests(ctx, job)
	if err != nil {
		return err
	}
	defer completion.Rollback()
	if err = completion.CompleteJob(ctx, job.ID, job.LeaseToken); err != nil {
		return err
	}
	return completion.Commit()
}
