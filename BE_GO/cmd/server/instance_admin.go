package main

import (
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/middleware"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/jmoiron/sqlx"
)

// composeInstanceAdmin builds the owner-only instance administration routes
// from the public bootstrap service (configuration body and administrator
// check) and the settings the Django views read.
func composeInstanceAdmin(cfg config.Config, db *sqlx.DB, bootstrap *service.InstanceBootstrap, actors *service.ActorAuthentication,
	identities *service.IdentityResolver, operations *paymentcategorylifecycle.Lifecycle) (*handler.InstanceAdminHandler, *service.InstanceAdmin) {
	settings := cfg.InstanceAdmin
	environment := service.InstanceAdminEnvironment{
		Version: cfg.Version, ConfiguredVersion: settings.ConfiguredVersion, DeploymentMode: settings.DeploymentMode, AppURL: settings.AppURL,
		EmailHost: settings.EmailHost, EmailUsername: settings.EmailUsername, EmailPassword: settings.EmailPassword,
		EmailPort: settings.EmailPort, EmailPortInvalid: settings.EmailPortInvalid, EmailUseTLS: settings.EmailUseTLS, EmailUseSSL: settings.EmailUseSSL,
		FromName: settings.FromName, FromAddress: settings.FromAddress, FromInvalid: settings.FromInvalid,
		AIAPIKey: settings.AIAPIKey, AIModel: settings.AIModel, AICheapModel: settings.AICheapModel, AIBaseURL: settings.AIBaseURL,
		AIIntegers: settings.AIIntegers, AIIntegersInvalid: settings.AIIntegersInvalid,
		StripePublicKey: settings.StripePublicKey, StripeKey: settings.StripeKey, StripeWebhookSecret: settings.StripeWebhookSecret,
		GoogleClientID: cfg.GoogleClientID, AppleClientID: cfg.AppleClientID, SecretKeys: []string{cfg.SessionSecret},
	}
	admin := service.NewInstanceAdmin(repository.NewInstanceAdmin(db), bootstrap, environment, time.Now)
	return handler.NewInstanceAdminHandler(admin, actors, identities, middleware.SessionAuditContext, operations), admin
}
