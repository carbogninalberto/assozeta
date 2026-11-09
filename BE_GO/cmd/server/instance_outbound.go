package main

import (
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/instanceprobe"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

// composeInstanceOutbound builds the release feed, e-mail test and
// integration test routes on the administration service. Outbound requests
// use the process proxy environment and SSL_CERT_FILE (see instanceprobe).
func composeInstanceOutbound(db *sqlx.DB, cache *redis.Client, admin *handler.InstanceAdminHandler, adminService *service.InstanceAdmin) (*handler.InstanceOutboundHandler, error) {
	client, err := instanceprobe.NewClient()
	if err != nil {
		return nil, err
	}
	releases, err := repository.NewReleaseCache(cache)
	if err != nil {
		return nil, err
	}
	outbound := service.NewInstanceOutbound(adminService, repository.NewInstanceAdmin(db), releases, service.SMTPTLS{Client: client}, time.Now)
	return handler.NewInstanceOutboundHandler(admin, outbound), nil
}
