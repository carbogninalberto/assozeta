package domain

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"
)

// ErrAuditRetentionConflict reports a definite PostgreSQL rollback at commit
// when a concurrently inserted index prevents parent deletion.
var ErrAuditRetentionConflict = errors.New("audit retention concurrent index conflict")

const JobDeleteOldAuditLogs = "delete_old_audit_logs"

type AuditRetentionStore interface {
	BeginAuditRetention(context.Context, Job) (AuditRetentionTransaction, error)
}

type AuditRetentionTransaction interface {
	DeleteExpiredAuditRecords(context.Context, time.Time) (int64, error)
	CompleteJob(context.Context, uuid.UUID, uuid.UUID) error
	Commit() error
	Rollback() error
}
