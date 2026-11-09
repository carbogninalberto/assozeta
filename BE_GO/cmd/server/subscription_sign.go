package main

import (
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/jmoiron/sqlx"
)

// composeSubscriptionSign wires subscription/sign: the signature upload goes
// through the document storage with S3Storage._save's write parameters.
func composeSubscriptionSign(cfg config.Config, db *sqlx.DB, tasks celery.Publisher, actors *service.ActorAuthentication,
	identities *service.IdentityResolver, audit handler.LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *handler.SubscriptionSignHandler {
	objects := documentUploadObjects{S3: composeDocumentObjects(cfg), cacheControl: cfg.Storage.CacheControl}
	if cfg.Storage.DefaultACL != nil {
		objects.acl = *cfg.Storage.DefaultACL
	}
	signs := service.NewSubscriptionSigns(repository.NewSubscriptionSigns(db), objects,
		service.SubscriptionSignSettings{UseObjectACL: cfg.Storage.UseObjectACL, PublicBaseURL: cfg.Storage.PublicBaseURL}, tasks, time.Now, nil)
	return handler.NewSubscriptionSignHandler(signs, actors, identities, audit, operations)
}
