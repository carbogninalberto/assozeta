package main

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"flag"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/middleware"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/jwt"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
	"github.com/labstack/echo/v4"
	"github.com/redis/go-redis/v9"
)

func main() {
	if err := run(); err != nil {
		slog.Error(err.Error())
		os.Exit(1)
	}
}
func run() error {
	envFile := flag.String("env-file", "", "optional path to the existing deployment .env")
	flag.Parse()
	cfg, err := config.Load(*envFile)
	if err != nil {
		return err
	}
	dbConfig, err := pgx.ParseConfig(cfg.DatabaseURL())
	if err != nil {
		return errors.New("invalid database configuration")
	}
	// Transaction poolers cannot retain prepared statements across transactions.
	dbConfig.DefaultQueryExecMode = pgx.QueryExecModeExec
	db := sqlx.NewDb(stdlib.OpenDB(*dbConfig), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(25)
	db.SetMaxIdleConns(5)
	db.SetConnMaxLifetime(30 * time.Minute)
	redisOptions := &redis.Options{Addr: cfg.RedisAddress, Username: cfg.RedisUsername, Password: cfg.RedisPassword,
		DB: 1, MaxRetries: -1, DialTimeout: 2 * time.Second, ReadTimeout: 2 * time.Second, WriteTimeout: 2 * time.Second, ContextTimeoutEnabled: true}
	if cfg.RedisTLS {
		redisOptions.TLSConfig = &tls.Config{MinVersion: tls.VersionTLS12}
		if cfg.RedisRootCertificate != "" {
			pem, err := os.ReadFile(cfg.RedisRootCertificate)
			if err != nil {
				return errors.New("cannot read Redis root certificate")
			}
			roots, err := x509.SystemCertPool()
			if err != nil {
				roots = x509.NewCertPool()
			}
			if !roots.AppendCertsFromPEM(pem) {
				return errors.New("invalid Redis root certificate")
			}
			redisOptions.TLSConfig.RootCAs = roots
		}
	}
	cache := redis.NewClient(redisOptions)
	defer cache.Close()
	tokens, err := jwt.New([]byte(cfg.JWTSigningKey), []byte(cfg.JWTVerifyingKey), time.Now)
	if err != nil {
		return errors.New("invalid JWT signing or verification configuration")
	}
	accounts := repository.NewAccounts(db)
	sessions, err := repository.NewDjangoImpersonationCache(cache)
	if err != nil {
		return err
	}
	djangoSessions, err := repository.NewDjangoSessionCache(cache)
	if err != nil {
		return err
	}
	sessionAuthentication := service.NewSessionAuthentication(djangoSessions, repository.NewSessionAccounts(db), cfg.SessionSecret)
	relations := repository.NewIdentityRelations(db)
	scopeGuard := service.NewScopedRequestGuard(repository.NewScopedResources(db), relations, sessions)
	identities := service.NewIdentityResolver(accounts, relations, sessions, service.NewPermissions(), scopeGuard)
	refreshHandler := handler.NewRefreshHandler(service.NewRefreshService(tokens), service.NewActorAuthentication(accounts, tokens), identities)
	identifierHandler := handler.NewIdentifierAvailabilityHandler(service.NewIdentifierAvailability(repository.NewIdentifierAccounts(db)), service.NewActorAuthentication(accounts, tokens), identities)
	twoFactorHandler := handler.NewTwoFactorInfoHandler(service.NewTwoFactorInfo(), service.NewActorAuthentication(accounts, tokens), identities)
	healthService := service.NewHealthService(repository.NewDatabaseProbe(db), repository.NewRedisProbe(cache), 2*time.Second)
	healthHandler := handler.NewHealthHandler(healthService, cfg.Version)
	e := echo.New()
	e.HideBanner = true
	e.HidePort = true
	// Caddy strips the external API prefix; register only upstream paths here.
	e.Any("/healthz", healthHandler.Health, middleware.DjangoCSRF(cfg.CSRFTrustedOrigins))
	e.Any("/readyz", healthHandler.Readiness, middleware.DjangoCSRF(cfg.CSRFTrustedOrigins))
	e.Any("/oauth2/refresh-token", refreshHandler.Refresh, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/oauth2/check/email", identifierHandler.Email, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/oauth2/check/username", identifierHandler.Username, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/two-fa/info", twoFactorHandler.Info, middleware.DjangoSessionAudit(sessionAuthentication))
	server := &http.Server{Addr: cfg.ListenAddress, Handler: e, ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 60 * time.Second}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	stopped := make(chan error, 1)
	go func() { stopped <- server.ListenAndServe() }()
	select {
	case err := <-stopped:
		if !errors.Is(err, http.ErrServerClosed) {
			return errors.New("HTTP server stopped unexpectedly")
		}
	case <-ctx.Done():
		drain, cancel := context.WithTimeout(context.Background(), 25*time.Second)
		defer cancel()
		if err := server.Shutdown(drain); err != nil {
			server.Close()
			return errors.New("HTTP drain deadline exceeded")
		}
	}
	return nil
}
