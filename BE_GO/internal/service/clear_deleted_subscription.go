package service

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"github.com/google/uuid"
)

// ClearDeletedSubscription is clear_deleted_subscription: each subscription
// marked deleted is removed in its own atomic block, with its course
// subscriptions and installments, the unpaid payments among theirs and the
// invoices only those payments used; paid payments and their invoices stay.
// After the block commits, its signature object is deleted from the default
// storage when no subscription references the key any more. A failing block
// is rolled back and logged, and the run goes on.
type ClearDeletedSubscription struct {
	store   domain.ClearDeletedSubscriptionStore
	objects domain.ExportObjects
	logger  *slog.Logger
}

func NewClearDeletedSubscription(store domain.ClearDeletedSubscriptionStore, objects domain.ExportObjects, logger *slog.Logger) *ClearDeletedSubscription {
	return &ClearDeletedSubscription{store: store, objects: objects, logger: logger}
}

func (s *ClearDeletedSubscription) Run(ctx context.Context, job domain.Job) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	var payload map[string]json.RawMessage
	if job.AssociationID != uuid.Nil || job.ID == uuid.Nil || job.LeaseToken == uuid.Nil || job.State != "running" ||
		job.Kind != domain.JobClearDeletedSubscription || job.Version != 1 || len(job.Payload) > 65536 ||
		json.Unmarshal(job.Payload, &payload) != nil || payload == nil || len(payload) != 0 {
		return domain.ErrJobInvalid
	}
	ids, err := s.deletedIDs(ctx, job)
	if err != nil {
		return err
	}
	removed := 0
	for _, id := range ids {
		if err := ctx.Err(); err != nil {
			return err
		}
		done, key, err := s.purge(ctx, job, id)
		if err != nil {
			return err
		}
		if !done {
			continue
		}
		removed++
		if key != nil && *key != "" {
			s.deleteSignature(ctx, job, *key)
		}
	}
	tx, err := s.store.BeginClearDeleted(ctx, job)
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
	s.logger.InfoContext(ctx, "clear_deleted_subscription: removed subscriptions", "subscriptions", removed)
	return nil
}

func (s *ClearDeletedSubscription) deletedIDs(ctx context.Context, job domain.Job) ([]uuid.UUID, error) {
	tx, err := s.store.BeginClearDeleted(ctx, job)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	ids, err := tx.DeletedSubscriptionIDs(ctx)
	if err != nil {
		return nil, err
	}
	s.logger.InfoContext(ctx, "clear_deleted_subscription: subscriptions to remove", "subscriptions", len(ids))
	return ids, tx.Commit()
}

// purge runs one atomic block; done is false when the row is gone or the
// block failed and was logged.
func (s *ClearDeletedSubscription) purge(ctx context.Context, job domain.Job, id uuid.UUID) (bool, *string, error) {
	tx, err := s.store.BeginClearDeleted(ctx, job)
	if err != nil {
		return false, nil, err
	}
	defer tx.Rollback()
	found, key, err := tx.PurgeDeletedSubscription(ctx, id)
	if err != nil {
		if rollbackErr := tx.Rollback(); rollbackErr != nil && ctx.Err() == nil {
			return false, nil, rollbackErr
		}
		if ctx.Err() != nil {
			return false, nil, ctx.Err()
		}
		if errors.Is(err, domain.ErrJobLeaseLost) {
			return false, nil, err
		}
		if errors.Is(err, domain.ErrClearDeletedUnsupported) {
			return false, nil, err
		}
		s.logger.WarnContext(ctx, "clear_deleted_subscription: could not remove subscription", "code", "subscription_clear_failed",
			"subscription_id", id.String())
		return false, nil, nil
	}
	if !found {
		return false, nil, nil
	}
	return true, key, tx.Commit()
}

// deleteSignature is the on_commit Subscription.delete_signature_key_if_unreferenced:
// every failure is logged and ignored.
func (s *ClearDeletedSubscription) deleteSignature(ctx context.Context, job domain.Job, name string) {
	referenced, err := func() (bool, error) {
		tx, err := s.store.BeginClearDeleted(ctx, job)
		if err != nil {
			return false, err
		}
		defer tx.Rollback()
		return tx.SignatureKeyReferenced(ctx, name)
	}()
	if err == nil && referenced {
		return
	}
	if err == nil {
		var key string
		if key, err = documentkey.S3Key(name, s.objects.Location()); err == nil {
			err = s.objects.Delete(ctx, key)
		}
	}
	if err != nil {
		s.logger.WarnContext(ctx, "Failed to delete unreferenced signature object", "code", "signature_delete_failed")
	}
}
