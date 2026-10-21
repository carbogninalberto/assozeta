package domain

import (
	"context"
	"time"

	"github.com/google/uuid"
)

const JobClearExpiredSubscriptionTokens = "clear_expired_subscription_tokens"

type SubscriptionTokenCleanupStore interface {
	BeginSubscriptionTokenCleanup(context.Context) (SubscriptionTokenCleanupTransaction, error)
}

type SubscriptionTokenCleanupTransaction interface {
	DeleteExpiredWithAudit(context.Context, time.Time, time.Time) (int64, error)
	CompleteJob(context.Context, uuid.UUID, uuid.UUID) error
	Commit() error
	Rollback() error
}
