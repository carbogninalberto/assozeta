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
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/crypto"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/instancelogo"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/s3credentials"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/s3trust"
	"github.com/jmoiron/sqlx"
)

// composeInstanceSetup builds the first-run setup, reconfigure and logo
// upload routes. The logo is written to the default (S3) storage with the
// same settings as the public logo read, through its own client.
func composeInstanceSetup(cfg config.Config, db *sqlx.DB, bootstrap *service.InstanceBootstrap, actors *service.ActorAuthentication,
	identities *service.IdentityResolver, operations *paymentcategorylifecycle.Lifecycle) *handler.InstanceSetupHandler {
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
	setup := service.NewInstanceSetup(repository.NewInstanceSetup(db), bootstrap, storage, crypto.NewPasswordVerifier(crypto.DefaultPasswordLimits()),
		service.InstanceSetupSettings{SetupToken: cfg.InstanceAdmin.SetupToken, CacheControl: settings.CacheControl, DefaultACL: settings.DefaultACL,
			FileOverwrite: settings.FileOverwrite, DisplayDefaults: repository.InstanceSetupDisplayDefaults()}, time.Now)
	return handler.NewInstanceSetupHandler(setup, actors, identities, middleware.SessionAuditContext, operations)
}
