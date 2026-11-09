package main

import (
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// composeSubscriptionImportUpload wires subscription/import/upload on the
// document upload storage (Document create and object PutObject).
func composeSubscriptionImportUpload(cfg config.Config, db *sqlx.DB, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit handler.LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *handler.SubscriptionImportUploadHandler {
	objects := documentUploadObjects{S3: composeDocumentObjects(cfg), cacheControl: cfg.Storage.CacheControl}
	if cfg.Storage.DefaultACL != nil {
		objects.acl = *cfg.Storage.DefaultACL
	}
	uploads := service.NewDocumentUploads(repository.NewDocumentUploads(db), objects, nil, cfg.Storage.FileOverwrite, time.Now, uuid.NewString)
	return handler.NewSubscriptionImportUploadHandler(service.NewSubscriptionImportUpload(repository.NewSubscriptionImport(db), uploads),
		actors, identities, audit, operations)
}
