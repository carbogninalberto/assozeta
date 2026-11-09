package main

import (
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/updaterrunner"
	passwordcrypto "github.com/carbogninalberto/assozeta/BE_GO/pkg/crypto"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

// composeInstanceBackups builds the owner's backup routes on the
// administration handler: the update runner (production mode,
// ASSOZETA_UPDATER_DIRECTORY), Django's cache, the S3 bucket the backup
// mirrors and the Django password hashers.
func composeInstanceBackups(cfg config.Config, db *sqlx.DB, cache *redis.Client, admin *handler.InstanceAdminHandler) (*handler.InstanceBackupsHandler, error) {
	store, err := repository.NewInstanceBackups(db, cache)
	if err != nil {
		return nil, err
	}
	runner := updaterrunner.Client{Production: cfg.InstanceAdmin.DeploymentMode == "production", Directory: cfg.InstanceAdmin.UpdaterDirectory}
	backups := service.NewInstanceBackups(store, runner, composeDocumentObjects(cfg),
		passwordcrypto.NewPasswordVerifier(passwordcrypto.DefaultPasswordLimits()), cfg.SessionSecret, time.Now,
		func() string { return strings.ReplaceAll(uuid.NewString(), "-", "") }) // uuid4().hex
	return handler.NewInstanceBackupsHandler(admin, backups), nil
}
