package main

import (
	"log/slog"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celeryworker"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/updaterrunner"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

// consumerBackupFailuresRegister adds instance.backups.notify_failures: the
// update runner (ASSOZETA_DEPLOYMENT_MODE, ASSOZETA_UPDATER_DIRECTORY), the
// Django cache (database 1 of the broker's Redis) and the system e-mail.
func consumerBackupFailuresRegister(tasks map[string]celeryworker.Task, cfg config.Config, db *sqlx.DB, broker *redis.Client,
	mail *service.SendMailAsync) error {
	cacheOptions := *broker.Options()
	cacheOptions.DB = 1
	store, err := repository.NewConsumerBackupFailures(db, redis.NewClient(&cacheOptions))
	if err != nil {
		return err
	}
	production := cfg.InstanceAdmin.DeploymentMode == "production"
	runner := updaterrunner.Client{Production: production, Directory: cfg.InstanceAdmin.UpdaterDirectory}
	notices := service.NewConsumerBackupFailures(store, runner, repository.NewRestoreStates(db), mail, production,
		strings.TrimSpace(cfg.RestoreLockPath) != "", cfg.DefaultFromEmail, time.Now, slog.Default())
	tasks[service.ConsumerBackupFailuresTask] = notices.Task()
	return nil
}
