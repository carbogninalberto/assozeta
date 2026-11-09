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

// composeCommunicationSendEmail wires communications/send/email: SMTP through
// smtpsend, the send_mail_async fallback through the Celery producer.
func composeCommunicationSendEmail(cfg config.Config, db *sqlx.DB, tasks celery.Publisher, actors *service.ActorAuthentication,
	identities *service.IdentityResolver, audit handler.LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *handler.CommunicationSendEmailHandler {
	return handler.NewCommunicationSendEmailHandler(service.NewCommunicationSendEmail(repository.NewCommunicationSendEmail(db), tasks,
		service.CommunicationSendEmailSettings{DefaultFromEmail: cfg.DefaultFromEmail, WhitelabelName: cfg.WhitelabelName}, time.Now),
		actors, identities, audit, operations)
}
