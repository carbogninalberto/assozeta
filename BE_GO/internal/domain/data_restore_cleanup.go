package domain

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/google/uuid"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// JobCleanupDataRestores is the Celery name of instance.tasks.cleanup_data_restores
// (@shared_task(name='instance.restore.cleanup')), as persisted Beat rows name it.
const JobCleanupDataRestores = "instance.restore.cleanup"

// ErrDataRestoreCleanupUnsupported refuses a run before any effect: a completed
// receipt's media is not a JSON object of strings, or a storage name the
// pruning would delete resolves outside AWS_LOCATION. Django raises midway
// through those runs, after deletes whose order follows a Python set.
var ErrDataRestoreCleanupUnsupported = refusal.New("data restore cleanup input outside the ported subset")

// ErrDataRestoreRecoveryBackups is int(DATA_RESTORE_RECOVERY_BACKUPS) raising
// ValueError: the task ends there, after the receipt loop.
var ErrDataRestoreRecoveryBackups = errors.New("DATA_RESTORE_RECOVERY_BACKUPS is not an integer")

// DataRestoreCleanupStore opens one READ COMMITTED transaction fenced by the
// job lease; each Django autocommit statement is one such transaction.
type DataRestoreCleanupStore interface {
	BeginDataRestoreCleanup(context.Context, Job) (DataRestoreCleanupTransaction, error)
}

type DataRestoreCleanupTransaction interface {
	// Terminal is DataRestore.objects.exclude(state__in=ACTIVE), unordered,
	// or ordered by -created_at, -id with a backup path when backups is set.
	Terminal(ctx context.Context, backups bool) ([]DataRestoreReceipt, error)
	// CompletedWithMedia is terminal.filter(state='completed').exclude(media={}).
	CompletedWithMedia(ctx context.Context) ([]DataRestoreReceipt, error)
	// ReferencedKeys is execution.referenced_restore_keys() without None.
	ReferencedKeys(ctx context.Context) (map[string]bool, error)
	// Save is save_operation: one UPDATE of the columns with updated_at.
	Save(ctx context.Context, id string, changes []DataRestoreColumn, at time.Time) error
	CompleteJob(ctx context.Context, id, token uuid.UUID, result json.RawMessage) error
	Commit() error
	Rollback() error
}

// DataRestoreColumn is one keyword of a save_operation call, in call order.
type DataRestoreColumn struct {
	Column string
	Value  any
}
