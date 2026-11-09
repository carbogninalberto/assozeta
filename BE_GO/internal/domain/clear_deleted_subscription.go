package domain

import (
	"context"
	"errors"

	"github.com/google/uuid"
)

// JobClearDeletedSubscription is the Celery name of clear_deleted_subscription.
const JobClearDeletedSubscription = "clear_deleted_subscription"

// ErrClearDeletedUnsupported ends the run when a subscription's deletion
// reaches a model the Collector port does not reproduce; that block is
// rolled back before its commit.
var ErrClearDeletedUnsupported = errors.New("clear_deleted_subscription reaches behaviour Go does not reproduce")

type ClearDeletedSubscriptionStore interface {
	// BeginClearDeleted opens a READ COMMITTED transaction fenced by the job
	// lease; each Django autocommit step or atomic block is one.
	BeginClearDeleted(context.Context, Job) (ClearDeletedSubscriptionTransaction, error)
}

type ClearDeletedSubscriptionTransaction interface {
	// DeletedSubscriptionIDs is Subscription._base_manager.filter(deleted=True).
	DeletedSubscriptionIDs(context.Context) ([]uuid.UUID, error)
	// PurgeDeletedSubscription locks the subscription if it is still marked
	// deleted and runs _purge_deleted_subscription; found is false when it is
	// gone or no longer deleted. key is its signature_storage_key.
	PurgeDeletedSubscription(context.Context, uuid.UUID) (found bool, key *string, err error)
	// SignatureKeyReferenced is Subscription._base_manager.filter(
	// signature_storage_key=key).exists().
	SignatureKeyReferenced(context.Context, string) (bool, error)
	CompleteJob(context.Context, uuid.UUID, uuid.UUID) error
	Commit() error
	Rollback() error
}
