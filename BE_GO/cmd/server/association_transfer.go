package main

import (
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/middleware"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

// composeAssociationTransfer builds association/export/* and
// association/import/*: Django's cache (DB 1) for export snapshots, the S3
// default storage, the Celery producer for export_association_data and
// import_association_data, and INSTANCE_SETUP_TOKEN for the setup wizard.
func composeAssociationTransfer(cfg config.Config, db *sqlx.DB, cache *redis.Client, tasks celery.Publisher,
	actors *service.ActorAuthentication, identities *service.IdentityResolver) *handler.AssociationTransferHandler {
	store := repository.NewAssociationTransfer(db, cache)
	objects := documentUploadObjects{S3: composeDocumentObjects(cfg), cacheControl: cfg.Storage.CacheControl}
	if cfg.Storage.DefaultACL != nil {
		objects.acl = *cfg.Storage.DefaultACL
	}
	transfer := service.NewAssociationTransfer(store, cfg.SessionSecret, time.Now).With(service.AssociationTransferOptions{
		Writes: store, Objects: objects, Tasks: tasks, SetupToken: cfg.InstanceAdmin.SetupToken,
		FileOverwrite: cfg.Storage.FileOverwrite, NewID: uuid.NewString})
	return handler.NewAssociationTransferHandler(transfer, actors, identities, middleware.SessionAuditContext)
}
