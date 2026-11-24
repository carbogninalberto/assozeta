package main

import (
	"context"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celeryworker"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/whatsapp"
	"github.com/jmoiron/sqlx"
)

func whatsappPollTask(cfg config.Config, db *sqlx.DB) celeryworker.Task {
	admin := service.NewInstanceAdmin(repository.NewInstanceAdmin(db), nil, service.InstanceAdminEnvironment{
		WhatsApp: cfg.WhatsApp, SecretKeys: []string{cfg.SessionSecret},
	}, time.Now)
	queue := whatsapp.NewQueue(db.DB, admin.WhatsAppSettings)
	return celeryworker.Task{MaxRetries: 3, Run: func(ctx context.Context, request *celeryworker.Request) (any, error) {
		if len(request.Args) != 0 || len(request.Kwargs) != 0 {
			return nil, &celeryworker.Exception{Type: "TypeError", Module: "builtins", Args: []any{"deliver_whatsapp takes no arguments"}}
		}
		return nil, queue.Drain(ctx, celeryworker.BrokerConsumerID(ctx))
	}}
}
