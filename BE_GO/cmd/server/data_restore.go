package main

import (
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

// composeDataRestore builds the data-restore administration routes on the
// administration handler: the receipts, the restore file lock
// (DATA_RESTORE_LOCK_PATH), Django's cache for progress snapshots, the S3
// default storage and the Celery producer for restore_instance_data.
func composeDataRestore(cfg config.Config, db *sqlx.DB, cache *redis.Client, tasks celery.Publisher,
	admin *handler.InstanceAdminHandler) (*handler.DataRestoreHandler, error) {
	progress, err := repository.NewDataRestoreProgress(cache)
	if err != nil {
		return nil, err
	}
	restore := service.NewDataRestore(repository.NewDataRestores(db), progress, composeDocumentObjects(cfg),
		repository.NewRestoreFileLock(cfg.RestoreLockPath), tasks, service.DataRestoreSettings{
			SecretKey: cfg.SessionSecret, Version: cfg.Version, LockConfigured: cfg.RestoreLockPath != "",
			MaxUploadBytes: cfg.DataRestoreMaxUploadBytes,
		}, time.Now)
	return handler.NewDataRestoreHandler(admin, restore), nil
}
