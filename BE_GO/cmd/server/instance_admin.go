package main

import (
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/instanceprobe"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/middleware"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/updaterrunner"
	"github.com/jmoiron/sqlx"
)

// composeInstanceAdmin builds the owner-only instance administration routes
// from the public bootstrap service (configuration body and administrator
// check) and the settings the Django views read.
func composeInstanceAdmin(cfg config.Config, db *sqlx.DB, bootstrap *service.InstanceBootstrap, actors *service.ActorAuthentication,
	identities *service.IdentityResolver, operations *paymentcategorylifecycle.Lifecycle) (*handler.InstanceAdminHandler, *service.InstanceAdmin) {
	admin := newInstanceAdmin(cfg, db, bootstrap)
	// Production updates and restarts: the update runner socket and the release catalog
	// (composeInstanceOutbound fails the start when the outbound client cannot be built).
	if releases, err := instanceprobe.NewClient(); err == nil {
		admin = admin.WithUpdates(updaterrunner.Client{Production: cfg.InstanceAdmin.DeploymentMode == "production", Directory: cfg.InstanceAdmin.UpdaterDirectory},
			releases)
	}
	return handler.NewInstanceAdminHandler(admin, actors, identities, middleware.SessionAuditContext, operations), admin
}

func newInstanceAdmin(cfg config.Config, db *sqlx.DB, bootstrap service.InstanceAdminBootstrap) *service.InstanceAdmin {
	settings := cfg.InstanceAdmin
	environment := service.InstanceAdminEnvironment{
		WhatsApp: cfg.WhatsApp, Version: cfg.Version, ConfiguredVersion: settings.ConfiguredVersion, DeploymentMode: settings.DeploymentMode, AppURL: settings.AppURL,
		EmailHost: settings.EmailHost, EmailUsername: settings.EmailUsername, EmailPassword: settings.EmailPassword,
		EmailPort: settings.EmailPort, EmailPortInvalid: settings.EmailPortInvalid, EmailUseTLS: settings.EmailUseTLS, EmailUseSSL: settings.EmailUseSSL,
		FromName: settings.FromName, FromAddress: settings.FromAddress,
		AIAPIKey: settings.AIAPIKey, AIModel: settings.AIModel, AICheapModel: settings.AICheapModel, AIBaseURL: settings.AIBaseURL,
		AIIntegers: settings.AIIntegers, AIIntegersInvalid: settings.AIIntegersInvalid,
		StripePublicKey: settings.StripePublicKey, StripeKey: settings.StripeKey, StripeWebhookSecret: settings.StripeWebhookSecret,
		GoogleClientID: cfg.GoogleClientID, AppleClientID: cfg.AppleClientID, SecretKeys: []string{cfg.SessionSecret},
	}
	return service.NewInstanceAdmin(repository.NewInstanceAdmin(db), bootstrap, environment, time.Now)
}
