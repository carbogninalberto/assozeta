package domain

import (
	"context"

	"github.com/google/uuid"
)

const JobResetDailyEmailBalance = "reset_communication_daily_email_balance"

// This is an instance-wide task, never an association-scoped HTTP operation.
type EmailBalanceResetStore interface {
	BeginEmailBalanceReset(context.Context) (EmailBalanceResetTransaction, error)
}

type EmailBalanceResetTransaction interface {
	ResetPositiveCounters(context.Context) (int64, error)
	CompleteJob(context.Context, uuid.UUID, uuid.UUID) error
	Commit() error
	Rollback() error
}
