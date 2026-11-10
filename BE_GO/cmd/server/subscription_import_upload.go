package main

import (
	"context"
	"errors"
	"io"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentobjects"
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
	return handler.NewSubscriptionImportUploadHandler(service.NewSubscriptionImportUpload(repository.NewSubscriptionImport(db), uploads,
		subscriptionImportObjects{S3: objects.S3}), actors, identities, audit, operations)
}

// subscriptionImportMaxBytes bounds the stored file read for an import (the
// wizard uploads files below 15 MB).
const subscriptionImportMaxBytes = 64 << 20

// subscriptionImportObjects is default_storage.open(name).read().
type subscriptionImportObjects struct{ *documentobjects.S3 }

func (o subscriptionImportObjects) Read(ctx context.Context, key string) ([]byte, error) {
	object, err := o.S3.Open(ctx, key)
	if err != nil {
		return nil, err
	}
	defer object.Body.Close()
	content, err := io.ReadAll(io.LimitReader(object.Body, subscriptionImportMaxBytes+1))
	if err != nil {
		return nil, err
	}
	if len(content) > subscriptionImportMaxBytes {
		return nil, errors.New("subscription import: stored file too large")
	}
	return content, nil
}
