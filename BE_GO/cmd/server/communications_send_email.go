package main

import (
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/middleware"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/jmoiron/sqlx"
	"github.com/labstack/echo/v4"
)

func registerCommunicationDelivery(e *echo.Echo, cfg config.Config, db *sqlx.DB, tasks celery.Publisher, actors *service.ActorAuthentication,
	identities *service.IdentityResolver, sessionAudit echo.MiddlewareFunc, operations *paymentcategorylifecycle.Lifecycle) {
	email := service.NewCommunicationSendEmail(repository.NewCommunicationSendEmail(db), tasks,
		service.CommunicationSendEmailSettings{DefaultFromEmail: cfg.DefaultFromEmail, WhitelabelName: cfg.WhitelabelName}, time.Now)
	e.Any("/communications/send/email", handler.NewCommunicationSendEmailHandler(email, actors, identities,
		middleware.SessionAuditContext, operations).Send, sessionAudit)

	templates := service.NewMessageTemplates(repository.NewMessageTemplates(db), map[string]service.MessageDelivery{
		"email": email.DeliverTemplate,
	}, time.Now)
	handler.NewMessageTemplatesHandler(templates, actors, identities, middleware.SessionAuditContext, operations).Register(e, sessionAudit)
}
