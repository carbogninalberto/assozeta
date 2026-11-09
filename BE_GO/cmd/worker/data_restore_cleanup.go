package main

import (
	"context"
	"io"
	"log/slog"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/jmoiron/sqlx"
)

// dataRestoreCleanupAdmission admits instance.restore.* jobs without the
// ordinary shared lease, pending-restore deferral or stale cutoff:
// CoordinatedTask.__call__ runs those task names directly, and
// cleanup_data_restores takes the exclusive restore lock itself.
type dataRestoreCleanupAdmission struct{}

func (dataRestoreCleanupAdmission) Acquire(context.Context) (io.Closer, *time.Time, error) {
	return nil, nil, nil
}

// composeDataRestoreCleanup builds the cleanup_data_restores executor: one
// slot, its own claim loop over the instance.restore.cleanup kind only.
func composeDataRestoreCleanup(cfg config.Config, db *sqlx.DB, jobs *repository.Jobs, logger *slog.Logger,
	timeout time.Duration, options service.JobExecutorOptions) (*service.JobExecutor, error) {
	cleanup := service.NewDataRestoreCleanup(repository.NewDataRestoreCleanups(db), composeExportCleanupObjects(cfg),
		repository.NewRestoreFileLock(cfg.RestoreLockPath), cfg.DataRestoreRecoveryBackups, time.Now, logger)
	options.Owner += "-restore-cleanup"
	options.Concurrency = 1
	return service.NewJobExecutor(jobs, dataRestoreCleanupAdmission{},
		map[string]service.JobDefinition{domain.JobCleanupDataRestores: {Version: 1, Timeout: timeout, Run: cleanup.Run}}, options)
}
