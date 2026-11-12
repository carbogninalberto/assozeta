package main

import (
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

// composeInstanceBackupActions builds the owner's backup actions (keep,
// delete, schedule, restore, upload, remote copy) on the administration
// handler: the update runner (production mode, ASSOZETA_UPDATER_DIRECTORY),
// Django's cache for the password attempts, the Django password hashers, the
// upload limit and the remote endpoint setting.
func composeInstanceBackupActions(cfg config.Config, db *sqlx.DB, cache *redis.Client, admin *handler.InstanceAdminHandler) (*handler.InstanceBackupActionsHandler, error) {
	store, err := repository.NewInstanceBackups(db, cache)
	if err != nil {
		return nil, err
	}
	runner := updaterrunner.Client{Production: cfg.InstanceAdmin.DeploymentMode == "production", Directory: cfg.InstanceAdmin.UpdaterDirectory}
	actions := service.NewInstanceBackupActions(store, runner, passwordcrypto.NewPasswordVerifier(passwordcrypto.DefaultPasswordLimits()),
		time.Now, uuid.NewString, // str(uuid4())
		service.BackupActionsLimits{UploadMaxBytes: cfg.InstanceAdmin.BackupUploadMaxBytes, AllowHTTP: cfg.InstanceAdmin.BackupRemoteAllowHTTP})
	return handler.NewInstanceBackupActionsHandler(admin, actions), nil
}
