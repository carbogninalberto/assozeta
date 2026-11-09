package service

import (
	"context"
	"encoding/json"
	"log/slog"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"github.com/google/uuid"
)

// Export retention constants of application.tasks (EXPORT_MAX_AGE_DAYS,
// EXPORT_MAX_COUNT).
const (
	exportCleanupMaxAge   = 30 * 24 * time.Hour
	exportCleanupMaxCount = 3
)

// ExportCleanup is cleanup_old_exports_task: cleanup_old_exports() for every
// association with an export archive row, returning {'deleted_count': n}.
type ExportCleanup struct {
	store   domain.ExportCleanupStore
	objects domain.ExportObjects
	now     func() time.Time
	logger  *slog.Logger
}

func NewExportCleanup(store domain.ExportCleanupStore, objects domain.ExportObjects, now func() time.Time, logger *slog.Logger) *ExportCleanup {
	return &ExportCleanup{store: store, objects: objects, now: now, logger: logger}
}

func (s *ExportCleanup) Run(ctx context.Context, job domain.Job) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	var payload map[string]json.RawMessage
	if job.AssociationID != uuid.Nil || job.ID == uuid.Nil || job.LeaseToken == uuid.Nil || job.State != "running" || job.Kind != domain.JobCleanupOldExports || job.Version != 1 || len(job.Payload) > 65536 || json.Unmarshal(job.Payload, &payload) != nil || payload == nil || len(payload) != 0 {
		return domain.ErrJobInvalid
	}
	// timezone.now() - timedelta(days=EXPORT_MAX_AGE_DAYS), compared in Python
	// with the loaded creation_date (microseconds).
	cutoff := s.now().UTC().Truncate(time.Microsecond).Add(-exportCleanupMaxAge)

	associations, err := s.plan(ctx, job)
	if err != nil {
		return err
	}
	deleted := 0
	for _, association := range associations {
		// The age pass iterates the queryset evaluated before its deletes.
		exports, err := s.read(ctx, job, association, 0)
		if err != nil {
			return err
		}
		for _, export := range exports {
			if !export.Created.Before(cutoff) {
				continue
			}
			done, err := s.delete(ctx, job, association, export, "age_exceeded")
			if err != nil {
				return err
			}
			if done {
				deleted++
			}
		}
		// list(remaining_exports[max_count:]) re-queries after the age pass.
		excess, err := s.read(ctx, job, association, exportCleanupMaxCount)
		if err != nil {
			return err
		}
		for _, export := range excess {
			done, err := s.delete(ctx, job, association, export, "count_exceeded")
			if err != nil {
				return err
			}
			if done {
				deleted++
			}
		}
	}
	tx, err := s.store.BeginExportCleanup(ctx, job)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = tx.CompleteJob(ctx, job.ID, job.LeaseToken, json.RawMessage(`{"deleted_count": `+strconv.Itoa(deleted)+`}`)); err != nil {
		return err
	}
	if err = tx.Commit(); err != nil {
		return err
	}
	s.logger.InfoContext(ctx, "old exports removed", "deleted_count", deleted)
	return nil
}

// plan reads the associations and refuses the run before any effect when an
// export document of any of them has a reference the collector would cascade.
// Every export row in scope is checked: which rows the count pass deletes
// depends on the outcome of the age pass.
func (s *ExportCleanup) plan(ctx context.Context, job domain.Job) ([]string, error) {
	tx, err := s.store.BeginExportCleanup(ctx, job)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	associations, err := tx.ExportAssociations(ctx)
	if err != nil {
		return nil, err
	}
	documents := []string{}
	for _, association := range associations {
		exports, err := tx.AssociationExports(ctx, association, 0)
		if err != nil {
			return nil, err
		}
		for _, export := range exports {
			documents = append(documents, export.DocumentID)
		}
	}
	conflict, err := tx.ExportReferenceConflict(ctx, documents)
	if err != nil {
		return nil, err
	}
	if conflict {
		s.logger.WarnContext(ctx, "export cleanup refused", "code", "export_cleanup_cascade")
		return nil, domain.ErrExportCleanupUnsupported
	}
	return associations, tx.Rollback()
}

func (s *ExportCleanup) read(ctx context.Context, job domain.Job, association string, offset int) ([]domain.ExportArchive, error) {
	tx, err := s.store.BeginExportCleanup(ctx, job)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	exports, err := tx.AssociationExports(ctx, association, offset)
	if err != nil {
		return nil, err
	}
	return exports, tx.Rollback()
}

// delete mirrors one guarded iteration: default_storage.delete(filepath) when
// it is set, export.delete(), document.delete(), each autocommitted. A storage
// error is logged and skips the item, as the task's except clause does.
func (s *ExportCleanup) delete(ctx context.Context, job domain.Job, association string, export domain.ExportArchive, reason string) (bool, error) {
	tx, err := s.store.BeginExportCleanup(ctx, job)
	if err != nil {
		return false, err
	}
	defer tx.Rollback()
	if export.Filepath != nil && *export.Filepath != "" {
		key, err := documentkey.S3Key(*export.Filepath, s.objects.Location())
		if err == nil {
			err = s.objects.Delete(ctx, key)
		}
		if err != nil {
			if ctx.Err() != nil {
				return false, ctx.Err()
			}
			s.logger.WarnContext(ctx, "export storage delete failed", "code", "export_storage_delete", "reason", reason)
			return false, nil
		}
	}
	at := s.now().UTC()
	if err = tx.DeleteExportArchive(ctx, export.ArchiveID, association, at); err != nil {
		return false, err
	}
	if err = tx.Commit(); err != nil {
		return false, err
	}
	document, err := s.store.BeginExportCleanup(ctx, job)
	if err != nil {
		return false, err
	}
	defer document.Rollback()
	if err = document.DeleteExportDocument(ctx, export.DocumentID, s.now().UTC()); err != nil {
		return false, err
	}
	return true, document.Commit()
}
