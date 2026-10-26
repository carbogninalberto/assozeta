package main

import (
	"context"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	ownedpgx "github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/middleware"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/redisclient"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	passwordcrypto "github.com/carbogninalberto/assozeta/BE_GO/pkg/crypto"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/jwt"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/manual"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/qrcode"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/loginimage"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jmoiron/sqlx"
	"github.com/labstack/echo/v4"
	"github.com/redis/go-redis/v9"
)

const (
	httpDrainTimeout         = 25 * time.Second
	operationCleanupTimeout  = 5 * time.Second
	httpDrainDeadlineMessage = "HTTP drain deadline exceeded"
)

type gracefulHTTPServer interface {
	Shutdown(context.Context) error
	Close() error
}

type admittedOperations interface {
	StopAdmission()
	Expire()
	Wait(context.Context) error
}

func main() {
	if err := run(); err != nil {
		slog.Error(err.Error())
		os.Exit(1)
	}
}
func run() (runErr error) {
	envFile := flag.String("env-file", "", "optional path to the existing deployment .env")
	queryObservationFile := flag.String("query-observation-file", "", "optional new private JSONL file for bounded query diagnostics")
	queryConnectionObservationFile := flag.String("query-connection-observation-file", "", "optional new private connection JSONL file; requires query-observation-file")
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
	queryObserver, closeQueryObservation, err := openQueryObservationFiles(*queryObservationFile, *queryConnectionObservationFile)
	if err != nil {
		return err
	}
	defer func() { runErr = errors.Join(runErr, closeQueryObservation(runErr == nil)) }()
	if queryObserver != nil {
		dbConfig.Tracer = queryObserver
	}
	var db *sqlx.DB
	if queryObserver != nil {
		db = sqlx.NewDb(ownedpgx.OpenObservedDB(*dbConfig, queryObserver), "pgx")
	} else {
		db = sqlx.NewDb(ownedpgx.OpenDB(*dbConfig), "pgx")
	}
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
	redisOptions.Dialer = redisclient.DialContext(redisOptions)
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
	executable, err := os.Executable()
	if err != nil {
		return errors.New("cannot resolve bundled image codec")
	}
	// All image-bearing routes share one process-wide admission pool.
	images, err := loginimage.NewProcessor(filepath.Join(filepath.Dir(executable), "image-codec"), loginimage.ProcessorLimits{
		Codec:       loginimage.JPEGLimits{MaxRasterBytes: 64 << 20, MaxEncodedBytes: 8 << 20, Concurrency: 2, Timeout: 10 * time.Second},
		MaxURIBytes: 16 << 20, MaxHeaderBytes: 8 << 20, MaxTailBytes: 8 << 20, MaxDecodeBytes: 64 << 20, MaxTransformBytes: 128 << 20,
	})
	if err != nil {
		return err
	}
	loginHandler, loginRate, closeProviders, err := composeLogin(cfg, db, cache, tokens, accounts, identities, images)
	if err != nil {
		return err
	}
	defer closeProviders()
	profileHandler := handler.NewProfileInfoHandler(service.NewProfileInfo(service.NewLoginProfiles(repository.NewLoginProfiles(db), time.Now), service.NewAssociationProfiles(repository.NewAssociationProfiles(db), time.Now), repository.NewProfileInfoDetails(db), images, domain.RegistrationChoices{Family: cfg.FamilyRegistrationEnabled, Federation: cfg.FederationRegistrationEnabled, Multiple: cfg.MultipleRegistrationEnabled}), service.NewActorAuthentication(accounts, tokens), identities)
	profilePasswordHandler := handler.NewProfilePasswordHandler(service.NewProfilePasswordUpdates(service.NewProfilePasswords(repository.NewProfilePasswords(db), passwordcrypto.NewPasswordVerifier(passwordcrypto.DefaultPasswordLimits()), 2), service.NewProfilePasswordResponses(service.NewLoginProfiles(repository.NewLoginProfiles(db), time.Now), service.NewAssociationProfiles(repository.NewAssociationProfiles(db), time.Now), images)), service.NewActorAuthentication(accounts, tokens), identities)
	profileTablesHandler := handler.NewProfileTablesHandler(service.NewProfileTables(repository.NewProfileTables(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext)
	profileUpdateHandler := handler.NewProfileUpdateHandler(
		service.NewProfileUpdates(service.NewProfileMutations(repository.NewProfileUpdates(db), images, time.Now),
			service.NewProfileUpdateResponses(service.NewLoginProfiles(repository.NewLoginProfiles(db), time.Now), service.NewAssociationProfiles(repository.NewAssociationProfiles(db), time.Now), images,
				domain.RegistrationChoices{Family: cfg.FamilyRegistrationEnabled, Federation: cfg.FederationRegistrationEnabled, Multiple: cfg.MultipleRegistrationEnabled})),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext)
	refreshHandler := handler.NewRefreshHandler(service.NewRefreshService(tokens), service.NewActorAuthentication(accounts, tokens), identities)
	identifierHandler := handler.NewIdentifierAvailabilityHandler(service.NewIdentifierAvailability(repository.NewIdentifierAccounts(db)), service.NewActorAuthentication(accounts, tokens), identities)
	twoFactorName := "None"
	if cfg.EmailHostSupport != nil {
		twoFactorName = *cfg.EmailHostSupport
	}
	twoFactorSetupHandler := handler.NewTwoFactorSetupHandler(service.NewTwoFactorSetup(rand.Reader, qrcode.New(), twoFactorName, cfg.WhitelabelName), service.NewActorAuthentication(accounts, tokens), identities)
	twoFactorHandler := handler.NewTwoFactorInfoHandler(service.NewTwoFactorInfo(), service.NewActorAuthentication(accounts, tokens), identities)
	twoFactorUpdateHandler := handler.NewTwoFactorUpdateHandler(service.NewTwoFactorUpdates(repository.NewTwoFactorUpdates(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext)
	retiredBillingHandler := handler.NewRetiredBillingHandler(service.NewRetiredSoftwareBilling(), service.NewActorAuthentication(accounts, tokens), identities)
	manualSource := manual.New(cfg.Manual)
	manualHandler := handler.NewManualReaderHandler(service.NewManualReader(repository.NewManualFeatures(db), manualSource), service.NewActorAuthentication(accounts, tokens), identities)
	paymentCategoryOperations := paymentcategorylifecycle.New()
	paymentCategoriesHandler := handler.NewPaymentCategoriesHandler(service.NewPaymentCategories(repository.NewPaymentCategories(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	paymentInfoHandler := handler.NewPaymentInfoHandler(service.NewPaymentInfo(repository.NewPaymentInfo(db)), service.NewActorAuthentication(accounts, tokens), identities)
	messageReadsHandler := handler.NewMessageReadsHandler(service.NewMessageReads(repository.NewMessageReads(db)), service.NewActorAuthentication(accounts, tokens), identities, handler.WithMessageListSpoolDirectory(cfg.MessageListSpoolDirectory))
	messageMutationsHandler := handler.NewMessageMutationsHandler(service.NewMessageMutations(repository.NewMessageMutations(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	collaboratorListHandler := handler.NewCollaboratorListHandler(service.NewCollaboratorList(repository.NewCollaboratorList(db)), service.NewActorAuthentication(accounts, tokens), identities)
	impersonationHandler := handler.NewImpersonationManagementHandler(service.NewImpersonationManagement(accounts, repository.NewImpersonationManagement(db), sessions, time.Now, uuid.NewString), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext)
	instanceService := service.NewInstanceBootstrap(repository.NewInstanceBootstrap(db), manualSource, service.InstanceBootstrapOptions{
		Version: cfg.Version, AIEnabled: cfg.AIEnabled, StripeEnabled: cfg.StripeEnabled, StripePublicKey: cfg.StripePublicKey,
		GoogleClientID: cfg.GoogleClientID, AppleClientID: cfg.AppleClientID, FamilyRegistration: cfg.FamilyRegistrationEnabled,
		FederationRegistration: cfg.FederationRegistrationEnabled, MultipleRegistration: cfg.MultipleRegistrationEnabled,
	})
	instanceHandler := handler.NewInstanceBootstrapHandler(instanceService, service.NewActorAuthentication(accounts, tokens), identities)
	instanceManifestHandler := handler.NewInstanceManifestHandler(service.NewInstanceManifest(repository.NewInstanceManifest(db)))
	instanceLogoHandler := composeInstanceLogo(cfg)

	healthService := service.NewHealthService(repository.NewDatabaseProbe(db), repository.NewRedisProbe(cache), 2*time.Second)
	healthHandler := handler.NewHealthHandler(healthService, cfg.Version)
	restoreAdmission := service.NewRestoreAdmission(repository.NewRestoreFileLock(cfg.RestoreLockPath), repository.NewRestoreStates(db), cfg.RestoreLockPath != "")
	e := echo.New()
	e.HideBanner = true
	e.HidePort = true
	// Django's CORS middleware admits preflight before URL resolution/session.
	e.Pre(middleware.QueryObservation(queryObserver), handler.InstanceBootstrapLocale(), middleware.DjangoCORS(cfg.CORSAllowAll, cfg.CORSAllowedOrigins), middleware.DjangoRestoreAdmission(restoreAdmission), middleware.DjangoSecurityHeaders())
	// Caddy strips the external API prefix; register only upstream paths here.
	e.Any("/collaborators/list", collaboratorListHandler.Read, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/messages/list", messageReadsHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/messages/:message_id/detail", messageReadsHandler.Detail, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/messages/add", messageMutationsHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/messages/:message_id/delete", messageMutationsHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/healthz", healthHandler.Health, middleware.DjangoCSRF(cfg.CSRFTrustedOrigins))
	e.Any("/readyz", healthHandler.Readiness, middleware.DjangoCSRF(cfg.CSRFTrustedOrigins))
	e.Any("/oauth2/login", loginHandler.Login, middleware.DjangoSessionAudit(sessionAuthentication), middleware.LoginRateLimit(cfg.RateLimitEnabled, loginRate))
	e.Any("/oauth2/refresh-token", refreshHandler.Refresh, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/oauth2/check/email", identifierHandler.Email, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/oauth2/check/username", identifierHandler.Username, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instance/status", instanceHandler.Status, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instance/config", instanceHandler.Config, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instance/manifest.json", instanceManifestHandler.Read, handler.ManualResponseEncoding(), middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instance/logo.png", instanceLogoHandler.Read, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instance/access", instanceHandler.Access, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/manuale/sections", manualHandler.Sections, handler.ManualResponseEncoding(), middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/manuale/assets/*", manualHandler.Asset, handler.ManualResponseEncoding(), middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/profile/info", profileHandler.Info, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/profile/update/password", profilePasswordHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/profile/update", profileUpdateHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/profile/settings/tables", profileTablesHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/two-fa/setup", twoFactorSetupHandler.Setup, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/two-fa/info", twoFactorHandler.Info, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/two-fa/update", twoFactorUpdateHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/billing/active-plan", retiredBillingHandler.ActivePlan, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/billing/checkout", retiredBillingHandler.Checkout, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/payment/category/list", paymentCategoriesHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/payment/category/add", paymentCategoriesHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/payment/category/:uid/update", paymentCategoriesHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/payment/category/:uid/delete", paymentCategoriesHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/payment/:uid/info", paymentInfoHandler.Info, middleware.DjangoSessionAudit(sessionAuthentication))
	for _, prefix := range []string{"association", "administration"} {
		e.Any("/"+prefix+"/impersonation/users", impersonationHandler.Users, middleware.DjangoSessionAudit(sessionAuthentication))
		e.Any("/"+prefix+"/impersonation", impersonationHandler.Session, middleware.DjangoSessionAudit(sessionAuthentication))
	}
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
		if err := drainHTTPServer(server, paymentCategoryOperations); err != nil {
			return err
		}
	}
	return nil
}

func drainHTTPServer(server gracefulHTTPServer, operations admittedOperations) error {
	operations.StopAdmission()
	drainContext, cancelDrain := context.WithTimeout(context.Background(), httpDrainTimeout)
	shutdownErr := server.Shutdown(drainContext)
	cancelDrain()
	if shutdownErr == nil {
		return nil
	}

	// Shutdown has stopped accepting connections at this point. Closing sockets
	// is necessary for requests that outlived the graceful deadline. Only then
	// may expiry revoke the detached admitted-operation contexts.
	_ = server.Close()
	operations.Expire()
	cleanupContext, cancelCleanup := context.WithTimeout(context.Background(), operationCleanupTimeout)
	defer cancelCleanup()
	if cleanupErr := operations.Wait(cleanupContext); cleanupErr != nil {
		return fmt.Errorf("%s: %w", httpDrainDeadlineMessage, errors.Join(shutdownErr, cleanupErr))
	}
	return errors.New(httpDrainDeadlineMessage)
}
