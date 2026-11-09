package main

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"os"
	"time"

	smithyhttp "github.com/aws/smithy-go/transport/http"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/associationexport"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celeryworker"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/channels"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

// consumerRestoreObjects adds S3Storage.delete and the "missing key" test of
// _is_missing_storage_file (HTTP 404, NoSuchKey) to the print storage.
type consumerRestoreObjects struct{ consumerPrintObjects }

func (o consumerRestoreObjects) Missing(err error) bool {
	var response *smithyhttp.ResponseError
	return errors.As(err, &response) && response.HTTPStatusCode() == 404
}

// consumerRestoreRegister adds export_association_data and import_association_data.
func consumerRestoreRegister(tasks map[string]celeryworker.Task, cfg config.Config, db *sqlx.DB, broker *redis.Client,
	producer celery.Publisher) error {
	cacheOptions := *broker.Options()
	cacheOptions.DB = 1
	cacheClient := redis.NewClient(&cacheOptions)
	cache, err := repository.NewConsumerRestoreCache(cacheClient)
	if err != nil {
		return err
	}
	database := func(ctx context.Context) (associationexport.Conn, func(), error) {
		config, err := pgx.ParseConfig(cfg.DatabaseURL())
		if err != nil {
			return nil, nil, err
		}
		config.ConnectTimeout = 5 * time.Second
		config.RuntimeParams["application_name"] = "assozeta-go-celery-consumer"
		config.RuntimeParams["timezone"] = "UTC"
		conn, err := pgx.ConnectConfig(ctx, config)
		if err != nil {
			return nil, nil, err
		}
		return conn, func() { _ = conn.Close(context.Background()) }, nil
	}
	export, err := service.NewConsumerExportAssociation(repository.NewConsumerRestoreExportStore(db), cache,
		consumerRestoreObjects{consumerPrintStorage(cfg)}, repository.NewCeleryResults(db), channels.NewLayer(broker, nil, nil, nil),
		producer, database, service.ConsumerExportSettings{WhitelabelName: cfg.WhitelabelName, DefaultFromEmail: cfg.DefaultFromEmail,
			TempDir: os.TempDir()}, cfg.Storage.FileOverwrite, time.Now, uuid.NewString)
	if err != nil {
		return err
	}
	tasks[service.ConsumerExportAssociationTask] = export.Task()
	imports, err := service.NewConsumerImportAssociation(db, consumerImportObjects{consumerRestoreObjects{consumerPrintStorage(cfg)}},
		repository.ImportEffectsAudit, service.ConsumerImportSettings{TempDir: os.TempDir(), PublicBaseURL: cfg.Storage.PublicBaseURL,
			Overwrite: cfg.Storage.FileOverwrite}, time.Now, uuid.NewString)
	if err != nil {
		return err
	}
	tasks[service.ConsumerImportAssociationTask] = imports.Task()
	limits := cfg.DataRestoreArchiveLimits
	restore, err := service.NewConsumerRestoreInstance(db, repository.NewRestoreFileLock(cfg.RestoreLockPath), export, imports, cache,
		channels.NewLayer(broker, nil, nil, nil), consumerImportObjects{consumerRestoreObjects{consumerPrintStorage(cfg)}},
		cfg.DataRestoreRecoveryBackups, service.ConsumerRestoreSettings{TempDir: os.TempDir(), Version: cfg.Version,
			Limits: service.RestoreLimits{Expanded: limits.Expanded, JSON: limits.JSON, File: limits.File}}, time.Now, uuid.NewString,
		slog.Default())
	if err != nil {
		return err
	}
	tasks[service.ConsumerRestoreInstanceTask] = restore.Task()
	return nil
}

// consumerImportObjects adds the streaming read import_association_data's
// _stage_stored_archive performs (download_fileobj into the temporary file).
type consumerImportObjects struct{ consumerRestoreObjects }

func (o consumerImportObjects) Download(ctx context.Context, key string, destination io.Writer) error {
	object, err := o.Open(ctx, key)
	if err != nil {
		return err
	}
	defer object.Body.Close()
	_, err = io.Copy(destination, object.Body)
	return err
}
