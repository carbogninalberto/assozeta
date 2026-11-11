package main

import (
	"context"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/middleware"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/instancelogo"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/s3credentials"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/s3trust"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// composeModulesResponses builds the module response routes. Attachments are
// written to the default (S3) storage with the same settings as the other
// default-storage writes, through their own client.
func composeModulesResponses(cfg config.Config, db *sqlx.DB, limits *handler.PublicFlowLimits, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	operations *paymentcategorylifecycle.Lifecycle) *handler.ModulesResponsesHandler {
	settings := cfg.Storage
	optional := func(value *string) string {
		if value == nil {
			return ""
		}
		return *value
	}
	storage := instancelogo.NewS3(instancelogo.S3Options{TempDir: cfg.LogoSpoolDirectory, Endpoint: settings.Endpoint, Region: settings.Region,
		Bucket: settings.Bucket, Location: settings.Location,
		Trust: func(ctx context.Context) (s3trust.Policy, error) {
			return s3credentials.LoadTrust(ctx, optional(settings.SessionProfile))
		},
		Credentials: func(ctx context.Context) (aws.CredentialsProvider, error) {
			if err := settings.ValidateBackend(); err != nil {
				return nil, err
			}
			return s3credentials.New(ctx, s3credentials.Options{AccessKey: settings.AccessKey, SecretKey: settings.SecretKey,
				SessionToken: optional(settings.SessionToken), Profile: optional(settings.SessionProfile)}, nil)
		}})
	store := repository.NewModulesResponses(db)
	responses := service.NewModulesResponses(store, store, service.NewAssociationProfiles(repository.NewAssociationProfiles(db), time.Now),
		storage, service.ModuleAttachmentSettings{CacheControl: settings.CacheControl, DefaultACL: settings.DefaultACL,
			FileOverwrite: settings.FileOverwrite, PublicUploadLimit: cfg.PublicFlows.MaxUploadBytes}, time.Now, uuid.NewString)
	return handler.NewModulesResponsesHandler(responses, actors, identities, middleware.SessionAuditContext, operations).
		WithPublicLimits(limits).
		WithExport(service.NewModulesExport(repository.NewModulesExport(db), time.Now))
}
