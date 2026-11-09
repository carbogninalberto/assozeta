package main

import (
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler/businessmcp"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/manual"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

// composeAgent wires ws/agent/ (application.chat.consumers.AgentConsumer).
func composeAgent(cfg config.Config, db *sqlx.DB, cache *redis.Client, admin *service.InstanceAdmin, manualSource *manual.Loader) (*handler.AgentWebSocket, error) {
	agentCache, err := repository.NewAgentCache(cache)
	if err != nil {
		return nil, err
	}
	return &handler.AgentWebSocket{Runtime: NewAgentRuntime(cfg, db, admin, manualSource), Cache: agentCache}, nil
}

// NewAgentRuntime is the agent's composition shared with cmd/run-agent's.
func NewAgentRuntime(cfg config.Config, db *sqlx.DB, admin *service.InstanceAdmin, manualSource *manual.Loader) *service.AgentRuntime {
	settings := cfg.InstanceAdmin
	return &service.AgentRuntime{
		Admin:       admin,
		Manual:      manualSource,
		ManualTools: service.NewManualTools(repository.NewManualToolContext(db), manualSource),
		Store:       repository.NewAgentStore(db),
		Business:    repository.NewBusinessMCPTools(db),
		Coordination: service.AgentCoordination{Lock: repository.NewRestoreFileLock(cfg.RestoreLockPath),
			States: repository.NewRestoreStates(db), Configured: cfg.RestoreLockPath != ""},
		Environment: service.AgentEnvironment{AIAPIKey: settings.AIAPIKey, AIAPIKeySet: settings.AIAPIKey != "",
			AIModel: settings.AIModel, AIBaseURL: settings.AIBaseURL},
		DefaultIterations: settings.AIIntegers[0], DefaultHistoryCap: settings.AIIntegers[5],
		ToolSchemas: businessmcp.AgentToolSchemas(),
		Now:         time.Now,
	}
}
