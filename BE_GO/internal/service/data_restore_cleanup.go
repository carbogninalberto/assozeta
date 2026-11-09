package service

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"math/big"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"github.com/google/uuid"
)

// DataRestoreCleanup is instance.tasks.cleanup_data_restores: under the
// exclusive restore lock, expire review receipts older than 24 hours, clean
// the temporary storage of every failed, cancelled and completed receipt,
// then execution.prune_storage(). Busy (lock held, path unset or the file
// unopenable) returns None without effects.
//
// The task name starts with instance.restore., so CoordinatedTask runs it
// without the shared lease, the pending-restore deferral or the stale check:
// the worker composes it on an executor without ordinary admission.
type DataRestoreCleanup struct {
	store   domain.DataRestoreCleanupStore
	objects DataRestoreStorage
	locks   repository.RestoreOperationLocker
	// recoveryBackups is os.environ.get('DATA_RESTORE_RECOVERY_BACKUPS');
	// nil when unset (Django's default 1).
	recoveryBackups *string
	now             func() time.Time
	logger          *slog.Logger
}

func NewDataRestoreCleanup(store domain.DataRestoreCleanupStore, objects DataRestoreStorage, locks repository.RestoreOperationLocker,
	recoveryBackups *string, now func() time.Time, logger *slog.Logger) *DataRestoreCleanup {
	return &DataRestoreCleanup{store: store, objects: objects, locks: locks, recoveryBackups: recoveryBackups, now: now, logger: logger}
}

const dataRestoreReviewExpiry = 24 * time.Hour

func (s *DataRestoreCleanup) Run(ctx context.Context, job domain.Job) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	var payload map[string]json.RawMessage
	if job.AssociationID != uuid.Nil || job.ID == uuid.Nil || job.LeaseToken == uuid.Nil || job.State != "running" || job.Kind != domain.JobCleanupDataRestores || job.Version != 1 || len(job.Payload) > 65536 || json.Unmarshal(job.Payload, &payload) != nil || payload == nil || len(payload) != 0 {
		return domain.ErrJobInvalid
	}
	lease, err := s.acquire(ctx)
	if err != nil {
		if errors.Is(err, domain.ErrRestoreBusy) || errors.Is(err, domain.ErrRestoreVolumeUnavailable) || errors.Is(err, domain.ErrRestoreLockRequired) {
			s.logger.InfoContext(ctx, "data restore cleanup skipped", "code", "restore_busy")
			return s.complete(ctx, job)
		}
		return err
	}
	defer lease.Close()
	if err = s.plan(ctx, job); err != nil {
		return err
	}
	tx, err := s.store.BeginDataRestoreCleanup(ctx, job)
	if err != nil {
		return err
	}
	receipts, err := tx.Terminal(ctx, false)
	_ = tx.Rollback()
	if err != nil {
		return err
	}
	for i := range receipts {
		op := &receipts[i]
		now := s.now().UTC().Truncate(time.Microsecond)
		if op.State == "review" && op.CreatedAt.Before(now.Add(-dataRestoreReviewExpiry)) {
			if err = s.save(ctx, job, op, []domain.DataRestoreColumn{{Column: "state", Value: "cancelled"}, {Column: "stage", Value: "expired"}}); err != nil {
				return err
			}
		}
		if op.State == "failed" || op.State == "cancelled" || op.State == "completed" {
			if err = s.cleanup(ctx, job, op); err != nil {
				return err
			}
		}
	}
	if err = s.prune(ctx, job); err != nil {
		return err
	}
	return s.complete(ctx, job)
}

// acquire is operation_lock(exclusive=True, required=True). The worker's
// ordinary admission takes the shared lock for each claim even when idle,
// which Django's idle workers never hold, so Busy is retried for up to one
// second before the task gives up as Django does.
func (s *DataRestoreCleanup) acquire(ctx context.Context) (io.Closer, error) {
	for attempt := 0; ; attempt++ {
		lease, err := s.locks.Acquire(ctx, true, true)
		if !errors.Is(err, domain.ErrRestoreBusy) || attempt == dataRestoreCleanupBusyRetries {
			return lease, err
		}
		timer := time.NewTimer(dataRestoreCleanupBusyDelay)
		select {
		case <-ctx.Done():
			timer.Stop()
			return nil, ctx.Err()
		case <-timer.C:
		}
	}
}

const (
	dataRestoreCleanupBusyRetries = 20
	dataRestoreCleanupBusyDelay   = 50 * time.Millisecond
)

func (s *DataRestoreCleanup) complete(ctx context.Context, job domain.Job) error {
	tx, err := s.store.BeginDataRestoreCleanup(ctx, job)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = tx.CompleteJob(ctx, job.ID, job.LeaseToken, nil); err != nil {
		return err
	}
	return tx.Commit()
}

// save is save_operation in its own fenced transaction, applied to op.
func (s *DataRestoreCleanup) save(ctx context.Context, job domain.Job, op *domain.DataRestoreReceipt, changes []domain.DataRestoreColumn) error {
	tx, err := s.store.BeginDataRestoreCleanup(ctx, job)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	now := s.now().UTC().Truncate(time.Microsecond)
	if err = tx.Save(ctx, op.ID, changes, now); err != nil {
		return err
	}
	if err = tx.Commit(); err != nil {
		return err
	}
	columns := make(repository.DataRestoreChanges, len(changes))
	for i, change := range changes {
		columns[i] = repository.DataRestoreChange{Column: change.Column, Value: change.Value}
	}
	dataRestoreApply(op, columns, now)
	return nil
}

// cleanup is execution.cleanup: a storage failure is logged and ends the
// receipt's cleanup (except Exception). Database failures go back to the
// executor.
func (s *DataRestoreCleanup) cleanup(ctx context.Context, job domain.Job, op *domain.DataRestoreReceipt) error {
	stopped := func(err error) error {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		s.logger.WarnContext(ctx, "restore temporary-file cleanup will be retried", "code", "restore_cleanup_storage")
		return nil
	}
	if op.UploadPath != "" {
		if err := dataRestoreDeleteObject(ctx, s.objects, op.UploadPath); err != nil {
			return stopped(err)
		}
		if err := s.save(ctx, job, op, []domain.DataRestoreColumn{{Column: "upload_path", Value: ""}}); err != nil {
			return err
		}
	}
	if err := dataRestoreCleanPrefix(ctx, s.objects, "restores/"+op.ID+"/upload"); err != nil {
		return stopped(err)
	}
	if op.State != "completed" {
		if err := dataRestoreCleanPrefix(ctx, s.objects, "restores/"+op.ID+"/media"); err != nil {
			return stopped(err)
		}
		return s.save(ctx, job, op, []domain.DataRestoreColumn{{Column: "media", Value: "{}"}})
	}
	return nil
}

// dataRestoreMedia decodes a completed receipt's media: a JSON object whose
// values are strings, in stored (jsonb) order.
func dataRestoreMedia(raw json.RawMessage) ([]string, map[string]string, bool) {
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	token, err := decoder.Token()
	if err != nil || token != json.Delim('{') {
		return nil, nil, false
	}
	order, values := []string{}, map[string]string{}
	for decoder.More() {
		key, err := decoder.Token()
		if err != nil {
			return nil, nil, false
		}
		var value string
		if err = decoder.Decode(&value); err != nil {
			return nil, nil, false
		}
		name := key.(string)
		if _, seen := values[name]; !seen {
			order = append(order, name)
		}
		values[name] = value
	}
	return order, values, true
}

// plan refuses, before any effect, the runs in which prune_storage would raise
// after a partial, set-ordered series of deletes: media that is not an object
// of strings, or a name it would delete outside AWS_LOCATION.
func (s *DataRestoreCleanup) plan(ctx context.Context, job domain.Job) error {
	tx, err := s.store.BeginDataRestoreCleanup(ctx, job)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	completed, err := tx.CompletedWithMedia(ctx)
	if err != nil {
		return err
	}
	referenced, err := tx.ReferencedKeys(ctx)
	if err != nil {
		return err
	}
	for _, op := range completed {
		_, media, ok := dataRestoreMedia(op.Media)
		if !ok {
			return s.refuse(ctx)
		}
		for _, key := range media {
			if referenced[key] {
				continue
			}
			if _, err := documentkey.S3Key(key, s.objects.Location()); err != nil {
				return s.refuse(ctx)
			}
		}
	}
	backups, err := tx.Terminal(ctx, true)
	if err != nil {
		return err
	}
	for _, op := range backups {
		if _, err := documentkey.S3Key(op.BackupPath, s.objects.Location()); err != nil {
			return s.refuse(ctx)
		}
	}
	return tx.Rollback()
}

func (s *DataRestoreCleanup) refuse(ctx context.Context) error {
	s.logger.WarnContext(ctx, "data restore cleanup refused", "code", "data_restore_cleanup_unsupported")
	return domain.ErrDataRestoreCleanupUnsupported
}

// recoveryRetained is int(os.environ.get('DATA_RESTORE_RECOVERY_BACKUPS', 1))
// (settings has no DATA_RESTORE_RECOVERY_BACKUPS): Python int() of a str,
// with surrounding whitespace, one sign and single underscores between digits.
func (s *DataRestoreCleanup) recoveryRetained() (*big.Int, error) {
	if s.recoveryBackups == nil {
		return big.NewInt(1), nil
	}
	text := strings.TrimSpace(*s.recoveryBackups)
	digits := strings.TrimLeft(text, "+-")
	if len(text)-len(digits) > 1 || digits == "" || strings.HasPrefix(digits, "_") || strings.HasSuffix(digits, "_") ||
		strings.Contains(digits, "__") || strings.Trim(digits, "0123456789_") != "" {
		return nil, domain.ErrDataRestoreRecoveryBackups
	}
	value, ok := new(big.Int).SetString(strings.ReplaceAll(text, "_", ""), 10)
	if !ok {
		return nil, domain.ErrDataRestoreRecoveryBackups
	}
	return value, nil
}

// prune is execution.prune_storage(). Storage errors propagate (the task
// raises), so they fail the job after the effects already made.
func (s *DataRestoreCleanup) prune(ctx context.Context, job domain.Job) error {
	tx, err := s.store.BeginDataRestoreCleanup(ctx, job)
	if err != nil {
		return err
	}
	referenced, err := tx.ReferencedKeys(ctx)
	if err == nil {
		var completed []domain.DataRestoreReceipt
		completed, err = tx.CompletedWithMedia(ctx)
		_ = tx.Rollback()
		if err != nil {
			return err
		}
		for i := range completed {
			if err = s.pruneMedia(ctx, job, &completed[i], referenced); err != nil {
				return err
			}
		}
	} else {
		_ = tx.Rollback()
		return err
	}
	retained, err := s.recoveryRetained()
	if err != nil {
		return err
	}
	if tx, err = s.store.BeginDataRestoreCleanup(ctx, job); err != nil {
		return err
	}
	backups, err := tx.Terminal(ctx, true)
	_ = tx.Rollback()
	if err != nil {
		return err
	}
	one := big.NewInt(1)
	for i := range backups {
		op := &backups[i]
		if op.State == "completed" && retained.Sign() > 0 {
			retained.Sub(retained, one)
			continue
		}
		if err = dataRestoreDeleteObject(ctx, s.objects, op.BackupPath); err != nil {
			return err
		}
		if err = s.save(ctx, job, op, []domain.DataRestoreColumn{{Column: "backup_path", Value: ""}, {Column: "backup_sha256", Value: ""}}); err != nil {
			return err
		}
	}
	// Retries replace backup_path; earlier attempts' archives are unreachable.
	if tx, err = s.store.BeginDataRestoreCleanup(ctx, job); err != nil {
		return err
	}
	terminal, err := tx.Terminal(ctx, false)
	_ = tx.Rollback()
	if err != nil {
		return err
	}
	for _, op := range terminal {
		_, files, err := dataRestoreListdir(ctx, s.objects, "restores/"+op.ID+"/recovery")
		if err != nil {
			return err
		}
		for _, name := range files {
			key := "restores/" + op.ID + "/recovery/" + name
			if key != op.BackupPath {
				if err = dataRestoreDeleteObject(ctx, s.objects, key); err != nil {
					return err
				}
			}
		}
	}
	return nil
}

// pruneMedia drops the attachment copies no record references any more.
func (s *DataRestoreCleanup) pruneMedia(ctx context.Context, job domain.Job, op *domain.DataRestoreReceipt, referenced map[string]bool) error {
	order, media, ok := dataRestoreMedia(op.Media)
	if !ok {
		return domain.ErrDataRestoreCleanupUnsupported
	}
	kept := []string{}
	deleted := map[string]bool{}
	for _, identity := range order {
		key := media[identity]
		if referenced[key] {
			kept = append(kept, identity)
		} else if !deleted[key] {
			deleted[key] = true
			if err := dataRestoreDeleteObject(ctx, s.objects, key); err != nil {
				return err
			}
		}
	}
	if len(kept) == len(order) {
		return nil
	}
	object := map[string]string{}
	for _, identity := range kept {
		object[identity] = media[identity]
	}
	encoded, err := json.Marshal(object)
	if err != nil {
		return err
	}
	s.logger.InfoContext(ctx, "removed superseded restore attachments", "count", len(order)-len(kept))
	return s.save(ctx, job, op, []domain.DataRestoreColumn{{Column: "media", Value: string(encoded)}})
}
