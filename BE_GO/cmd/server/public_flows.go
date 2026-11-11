package main

import (
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/redis/go-redis/v9"
)

// composePublicFlowLimits builds the limits of the flows open without an
// account; the request counts live in the DRF throttle cache.
func composePublicFlowLimits(cfg config.Config, cache *redis.Client) (*handler.PublicFlowLimits, error) {
	throttle, err := repository.NewDRFThrottleCache(cache)
	if err != nil {
		return nil, err
	}
	return &handler.PublicFlowLimits{Store: throttle, Requests: int(cfg.PublicFlows.RequestsPerWindow),
		WindowSeconds: int(cfg.PublicFlows.WindowSeconds), MaxUploadBytes: cfg.PublicFlows.MaxUploadBytes, Now: time.Now}, nil
}
