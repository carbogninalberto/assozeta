package main

import (
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/importeffects"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/google/uuid"
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
	objects := documentUploadObjects{S3: composeDocumentObjects(cfg), cacheControl: cfg.Storage.CacheControl}
	if cfg.Storage.DefaultACL != nil {
		objects.acl = *cfg.Storage.DefaultACL
	}
	plan, err := importeffects.LoadPlan()
	if err != nil {
		return nil, err
	}
	archives := service.DataRestoreArchives{Plan: plan, FileOverwrite: cfg.Storage.FileOverwrite, Uploads: objects, NewID: uuid.NewString}
	archives.Limits.Expanded, archives.Limits.JSON, archives.Limits.File = cfg.DataRestoreArchiveLimits.Expanded,
		cfg.DataRestoreArchiveLimits.JSON, cfg.DataRestoreArchiveLimits.File
	restore := service.NewDataRestore(repository.NewDataRestores(db), progress, objects.S3,
		repository.NewRestoreFileLock(cfg.RestoreLockPath), tasks, service.DataRestoreSettings{
			SecretKey: cfg.SessionSecret, Version: cfg.Version, LockConfigured: cfg.RestoreLockPath != "",
			MaxUploadBytes: cfg.DataRestoreMaxUploadBytes,
		}, time.Now).WithArchives(archives)
	return handler.NewDataRestoreHandler(admin, restore), nil
}
