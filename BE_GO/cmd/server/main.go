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
	mathrand "math/rand/v2"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"sync"
	"syscall"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/channels"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	ownedpgx "github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/googleapi"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/middleware"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pdfrender"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/redisclient"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/stripeapi"
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
	// Django's CELERY_BROKER_URL is the same Redis without a database number (DB 0).
	brokerOptions := *redisOptions
	brokerOptions.DB = 0
	brokerOptions.Dialer = redisclient.DialContext(&brokerOptions)
	broker := redis.NewClient(&brokerOptions)
	defer broker.Close()
	tasks := celery.NewProducer(celery.RedisBroker{Client: broker})
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
	profileSettingsHandler := handler.NewProfileSettingsHandler(service.NewProfileSettings(repository.NewProfileSettings(db),
		service.NewMembershipCardConfigurations(repository.NewMembershipCardConfigurations(db), time.Now), time.Now),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	messageMutationsHandler := handler.NewMessageMutationsHandler(service.NewMessageMutations(repository.NewMessageMutations(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	workflowWritesHandler := handler.NewAutomationWorkflowWritesHandler(service.NewAutomationWorkflowWrites(repository.NewAutomationWorkflows(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	workflowReadsHandler := handler.NewAutomationWorkflowReadsHandler(service.NewAutomationWorkflowReads(repository.NewAutomationWorkflows(db)), service.NewActorAuthentication(accounts, tokens), identities)
	profileMiscHandler := handler.NewProfileMiscHandler(service.NewProfileMisc(repository.NewProfileMisc(db), repository.NewProfileMisc(db), time.Now),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	associationTransferHandler := handler.NewAssociationTransferHandler(service.NewAssociationTransfer(repository.NewAssociationTransfer(db, cache),
		cfg.SessionSecret, time.Now), service.NewActorAuthentication(accounts, tokens), identities)
	testimonialsHandler := handler.NewTestimonialsHandler(service.NewTestimonials(repository.NewTestimonials(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations, cfg.SenjaWebhookSecret)
	moduleTemplatesHandler := handler.NewModuleTemplatesHandler(service.NewModuleTemplates(repository.NewModuleTemplates(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	dashboardLayoutHandler := handler.NewDashboardLayoutHandler(service.NewDashboardLayouts(repository.NewDashboardLayouts(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	associationListHandler := handler.NewAssociationListHandler(service.NewAssociationList(repository.NewAssociationList(db)), service.NewActorAuthentication(accounts, tokens), identities)
	associationAdminHandler := handler.NewAssociationAdminHandler(service.NewAssociationAdmin(repository.NewAssociationAdmin(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	balanceAccountsHandler := handler.NewBalanceAccountsHandler(service.NewBalanceAccounts(repository.NewBalanceAccounts(db)), service.NewBalanceSheets(repository.NewBalanceAccounts(db), service.NewAssociationProfiles(repository.NewAssociationProfiles(db), time.Now)), service.NewActorAuthentication(accounts, tokens), identities)
	searchProfileHandler := handler.NewSearchProfileHandler(service.NewSearchProfiles(repository.NewSearchProfiles(db),
		service.NewAssociationProfiles(repository.NewAssociationProfiles(db), time.Now), images,
		service.NewStripeAvailability(repository.NewSocialConfiguration(db), service.StripeEnvironment{PublicKey: cfg.StripePublicKey != "", Secret: cfg.StripeSecretConfigured, Webhook: cfg.StripeEnabled}, cfg.SessionSecret),
		domain.RegistrationChoices{Family: cfg.FamilyRegistrationEnabled, Federation: cfg.FederationRegistrationEnabled, Multiple: cfg.MultipleRegistrationEnabled}, time.Now),
		service.NewActorAuthentication(accounts, tokens), identities)
	auditLogHandler := handler.NewAuditLogHandler(service.NewAuditLogs(repository.NewAuditLogs(db)), service.NewActorAuthentication(accounts, tokens), identities)
	auditSummaryHandler := handler.NewAuditSummaryHandler(service.NewAuditSummaries(repository.NewAuditSummaries(db)), service.NewActorAuthentication(accounts, tokens), identities)
	personasService := service.NewPersonas(repository.NewPersonas(db), time.Now)
	personasHandler := handler.NewPersonasHandler(personasService, service.NewActorAuthentication(accounts, tokens), identities)
	suppliersHandler := handler.NewSuppliersHandler(service.NewSuppliers(repository.NewSuppliers(db)), service.NewActorAuthentication(accounts, tokens), identities)
	carnetListHandler := handler.NewCarnetListHandler(service.NewCarnetLists(repository.NewCarnetList(db), time.Now), service.NewCarnetInfos(repository.NewCarnetList(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities)
	inconsistenciesHandler := handler.NewInconsistenciesHandler(service.NewInconsistencies(repository.NewInconsistencies(db), time.Now, time.Local), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	supplierInvoiceStatsHandler := handler.NewSupplierInvoiceStatsHandler(service.NewSupplierInvoiceStats(repository.NewSupplierInvoiceStats(db), time.Now, time.Local), service.NewActorAuthentication(accounts, tokens), identities)
	customerInvoiceStatsHandler := handler.NewCustomerInvoiceStatsHandler(service.NewCustomerInvoiceStats(repository.NewSupplierInvoiceStats(db), time.Now, time.Local), service.NewActorAuthentication(accounts, tokens), identities)
	invoiceDeletesHandler := handler.NewInvoiceDeletesHandler(service.NewInvoiceDeletes(repository.NewInvoiceDeletes(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	foldersHandler := handler.NewFoldersHandler(service.NewFolders(repository.NewFolders(db)), service.NewFolderDeletes(repository.NewFolders(db)), service.NewFolderMoves(repository.NewFolders(db)), service.NewActorAuthentication(accounts, tokens), identities, paymentCategoryOperations)
	folderWritesHandler := handler.NewFolderWritesHandler(service.NewFolderWrites(repository.NewFolders(db), time.Now),
		service.NewFolderDeletes(repository.NewFolders(db)), service.NewActorAuthentication(accounts, tokens), identities, paymentCategoryOperations)
	documentArchiveHandler := handler.NewDocumentArchiveHandler(service.NewDocumentArchive(repository.NewDocumentArchive(db)), service.NewArchiveWrites(repository.NewDocumentArchive(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	savedReportsHandler := handler.NewSavedReportsHandler(service.NewSavedReports(repository.NewSavedReports(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities, paymentCategoryOperations)
	courseLocationListHandler := handler.NewCourseLocationListHandler(service.NewCourseLocationLists(repository.NewSearchProfiles(db)), service.NewActorAuthentication(accounts, tokens), identities)
	courseTagListHandler := handler.NewCourseTagListHandler(service.NewCourseTagLists(repository.NewSearchProfiles(db)), service.NewActorAuthentication(accounts, tokens), identities)
	instructorListHandler := handler.NewInstructorListHandler(service.NewInstructorLists(repository.NewInstructorLists(db)), service.NewInstructorInfos(repository.NewInstructorLists(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities)
	carnetAssigns := service.NewCarnetAssigns(repository.NewCarnetWrites(db), time.Now, uuid.NewString)
	carnetWritesHandler := handler.NewCarnetWritesHandler(service.NewCarnetAdds(repository.NewCarnetWrites(db), carnetAssigns, time.Now, uuid.NewString), service.NewCarnetReplaces(repository.NewCarnetWrites(db), carnetAssigns, time.Now), carnetAssigns, service.NewCarnetTopups(repository.NewCarnetWrites(db), time.Now, uuid.NewString), service.NewCarnetMetas(repository.NewCarnetWrites(db), time.Now), service.NewCarnetLinks(repository.NewCarnetWrites(db)), service.NewCarnetToggles(repository.NewCarnetWrites(db), time.Now), service.NewCarnetDeletes(repository.NewCarnetWrites(db), time.Now), service.NewCarnetUpdates(repository.NewCarnetWrites(db), time.Now), service.NewCarnetUnassigns(repository.NewCarnetWrites(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	paymentArchiveHandler := handler.NewPaymentArchiveHandler(service.NewPaymentArchives(repository.NewPaymentArchives(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	attendanceDaysHandler := handler.NewAttendanceDaysHandler(service.NewAttendanceDays(repository.NewAttendanceDays(db), time.Now), service.NewAttendanceMarks(repository.NewAttendanceDays(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	statisticReadsHandler := handler.NewStatisticReadsHandler(service.NewStatisticReads(repository.NewStatisticReads(db), time.Now, time.Local), service.NewActorAuthentication(accounts, tokens), identities)
	communicationSMTPHandler := handler.NewCommunicationSMTPHandler(service.NewCommunicationSMTP(repository.NewCommunicationSMTP(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	emailLogsHandler := handler.NewEmailLogsHandler(service.NewEmailLogs(repository.NewEmailLogs(db), time.Now, time.Local), service.NewActorAuthentication(accounts, tokens), identities)
	staffBoardHandler := handler.NewStaffBoardHandler(service.NewStaffBoard(repository.NewStaffBoard(db), time.Now),
		service.NewActorAuthentication(accounts, tokens), identities, paymentCategoryOperations)
	collaboratorListHandler := handler.NewCollaboratorListHandler(service.NewCollaboratorList(repository.NewCollaboratorList(db)), service.NewActorAuthentication(accounts, tokens), identities)
	collaboratorWritesHandler := handler.NewCollaboratorWritesHandler(service.NewCollaboratorUpdates(repository.NewCollaboratorUpdates(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	impersonationHandler := handler.NewImpersonationManagementHandler(service.NewImpersonationManagement(accounts, repository.NewImpersonationManagement(db), sessions, time.Now, uuid.NewString), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext)
	instanceService := service.NewInstanceBootstrap(repository.NewInstanceBootstrap(db), manualSource, service.InstanceBootstrapOptions{
		Version: cfg.Version, AIEnabled: cfg.AIEnabled, StripeEnabled: cfg.StripeEnabled, StripePublicKey: cfg.StripePublicKey,
		GoogleClientID: cfg.GoogleClientID, AppleClientID: cfg.AppleClientID, FamilyRegistration: cfg.FamilyRegistrationEnabled,
		FederationRegistration: cfg.FederationRegistrationEnabled, MultipleRegistration: cfg.MultipleRegistrationEnabled,
	})
	instanceHandler := handler.NewInstanceBootstrapHandler(instanceService, service.NewActorAuthentication(accounts, tokens), identities)
	instanceAdminHandler, instanceAdminService := composeInstanceAdmin(cfg, db, instanceService, service.NewActorAuthentication(accounts, tokens), identities, paymentCategoryOperations)
	instanceOutboundHandler, err := composeInstanceOutbound(db, cache, instanceAdminHandler, instanceAdminService)
	if err != nil {
		return err
	}
	bakneySSOHandler, closeBakneySSO, err := composeBakneySSO(cfg, db, cache, tokens, accounts, identities, instanceService, images)
	if err != nil {
		return err
	}
	defer closeBakneySSO()
	instanceManifestHandler := handler.NewInstanceManifestHandler(service.NewInstanceManifest(repository.NewInstanceManifest(db)))
	instanceLogoHandler := composeInstanceLogo(cfg)
	instanceSetupHandler := composeInstanceSetup(cfg, db, instanceService, service.NewActorAuthentication(accounts, tokens), identities, paymentCategoryOperations)
	docmanagerReadsHandler := handler.NewDocmanagerReadsHandler(service.NewDocmanagerReads(repository.NewDocmanagerReads(db),
		composeDocumentObjects(cfg), service.NewActorAuthentication(accounts, tokens), cfg.SessionSecret, time.Now),
		service.NewActorAuthentication(accounts, tokens), identities)

	// document/<kind>/<uid> PDF routes: the Puppeteer FastRender service (PUPPETEER_HOST/PORT) prints
	// CURRENT_HOST_PUPPETEER/<view>; DOCUMENT_BYPASS_TOKEN is HasBypassAuthorizationHeader's token.
	// document/<kind>/<uid>/view/ HTML views (the pages the renderer prints) and the subscription preview.
	documentViews := service.NewDocumentViews(repository.NewDocumentViews(db),
		pdfrender.New(os.Getenv("PUPPETEER_HOST"), os.Getenv("PUPPETEER_PORT"), os.Getenv("CURRENT_HOST_PUPPETEER")),
		os.Getenv("DOCUMENT_BYPASS_TOKEN"), time.Now)
	documentViewsHandler := handler.NewDocumentViewsHandler(documentViews, service.NewActorAuthentication(accounts, tokens), identities)
	// document/invoice/<uid>/view/ and document/einvoice/<uid>/view/ (the FatturaPA page of internal/fatturapa).
	invoiceViewsHandler := handler.NewInvoiceViewsHandler(service.NewInvoiceViews(repository.NewInvoiceViews(db), documentViews),
		service.NewActorAuthentication(accounts, tokens), identities)
	documentRenderHandler := handler.NewDocumentRenderHandler(service.NewDocumentRender(repository.NewDocumentRender(db),
		pdfrender.New(os.Getenv("PUPPETEER_HOST"), os.Getenv("PUPPETEER_PORT"), os.Getenv("CURRENT_HOST_PUPPETEER")), tasks,
		documentRenderSettings(cfg), time.Now, uuid.NewString),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)

	apiSchemaHandler := handler.NewAPISchemaHandler(service.NewActorAuthentication(accounts, tokens), identities, cfg.CSRFCookieSecure)
	documentUploadsHandler := composeDocumentUploads(cfg, db, sessions, service.NewActorAuthentication(accounts, tokens), identities,
		middleware.SessionAuditContext, paymentCategoryOperations)
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
	e.Any("/collaborators/:uid/update", collaboratorWritesHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/messages/list", messageReadsHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/messages/:message_id/detail", messageReadsHandler.Detail, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/messages/add", messageMutationsHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/messages/:message_id/delete", messageMutationsHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/workflows/add", workflowWritesHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/testimonials/add", testimonialsHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/testimonials/update", testimonialsHandler.Webhook, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/statistic/dashboard/layout", dashboardLayoutHandler.Set, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/statistic/dashboard", statisticReadsHandler.Dashboard, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/statistic/athlete-dashboard", statisticReadsHandler.Athlete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/statistic/report", statisticReadsHandler.Report, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/sport-associations/list", associationListHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/check-inconsistencies", inconsistenciesHandler.Check, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/carnet/list", carnetListHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/carnet-subscription/list", handler.NewCarnetSubscriptionListHandler(service.NewCarnetSubscriptionLists(repository.NewCarnetSubscriptionLists(db)), service.NewActorAuthentication(accounts, tokens), identities).List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/carnet/add", carnetWritesHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/carnet/:uid/info", carnetListHandler.Info, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/carnet/:uid/delete", carnetWritesHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/carnet/:uid/update", carnetWritesHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/carnet-subscription/:uid/disable", carnetWritesHandler.Disable, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/carnet-subscription/:uid/enable", carnetWritesHandler.Enable, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/carnet-subscription/:uid/update", carnetWritesHandler.UpdateLessons, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/carnet-subscription/:uid/topup", carnetWritesHandler.Topup, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/carnet/:uid/assign/:uid_subscription", carnetWritesHandler.Assign, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/carnet/:uid/replace/:uid_subscription", carnetWritesHandler.Replace, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/carnet-subscription/:uid/delete/:uid_course", carnetWritesHandler.RemoveCourseLink, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/carnet/:uid/unassign/:uid_subscription", carnetWritesHandler.Unassign, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/search/profile/:username", searchProfileHandler.Profile, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/payment/:uid/archive", paymentArchiveHandler.Archive, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instructor/list", instructorListHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/course/tags/list", courseTagListHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	courseTagWritesHandler := handler.NewCourseTagWritesHandler(service.NewCourseTagWrites(repository.NewCourseTagWrites(db), time.Now, uuid.NewString),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/course/tags/add", courseTagWritesHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/course/tags/:tag_id/update", courseTagWritesHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/course/tags/:tag_id/delete", courseTagWritesHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/course/tags/:tag_id/assign/:course_id", courseTagWritesHandler.Assign, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/course/tags/:tag_id/unassign/:course_id", courseTagWritesHandler.Unassign, middleware.DjangoSessionAudit(sessionAuthentication))
	subscriptionTagsHandler := handler.NewSubscriptionTagsHandler(service.NewSubscriptionTags(repository.NewSubscriptionTags(db), time.Now, uuid.NewString),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/subscription/tags/list", subscriptionTagsHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/tags/add", subscriptionTagsHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/tags/:tag_id/update", subscriptionTagsHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/tags/:tag_id/delete", subscriptionTagsHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/tags/:tag_id/assign/:subscription_id", subscriptionTagsHandler.Assign, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/tags/:tag_id/unassign/:subscription_id", subscriptionTagsHandler.Unassign, middleware.DjangoSessionAudit(sessionAuthentication))
	subscriptionLifecycleHandler := handler.NewSubscriptionLifecycleHandler(service.NewSubscriptionLifecycles(repository.NewSubscriptionLifecycles(db), time.Now),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/subscription/:uid/reject", subscriptionLifecycleHandler.Reject, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/:uid/archive", subscriptionLifecycleHandler.Archive, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/:uid/delete", subscriptionLifecycleHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	subscriptionApproveHandler := handler.NewSubscriptionApproveHandler(service.NewSubscriptionApprovals(repository.NewSubscriptionLifecycles(db), tasks, time.Now),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/subscription/:uid/approve", subscriptionApproveHandler.Approve, middleware.DjangoSessionAudit(sessionAuthentication))
	subscriptionUpdateHandler := handler.NewSubscriptionUpdateHandler(service.NewSubscriptionUpdates(repository.NewSubscriptionLifecycles(db), tasks, time.Now),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/subscription/:uid/update", subscriptionUpdateHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	subscriptionReminderHandler := handler.NewSubscriptionMedicalReminderHandler(service.NewSubscriptionMedicalReminders(repository.NewSubscriptionMedical(db),
		tasks, service.MedicalReminderSettings{AppURL: cfg.InstanceAdmin.AppURL, WhitelabelName: cfg.WhitelabelName, IsWhitelabel: cfg.IsWhitelabel}),
		service.NewActorAuthentication(accounts, tokens), identities, paymentCategoryOperations)
	e.Any("/subscription/:uid/medical-certificate/send-email-reminder", subscriptionReminderHandler.Send, middleware.DjangoSessionAudit(sessionAuthentication))
	emailRenderHandler := handler.NewEmailRenderHandler(service.NewEmailRenderRoutes(repository.NewEmailRenderRoutes(db), tasks,
		service.EmailRenderSettings{AppURL: cfg.InstanceAdmin.AppURL, WhitelabelName: cfg.WhitelabelName, DefaultFromEmail: cfg.DefaultFromEmail,
			DefaultTeamEmail: cfg.DefaultTeamEmail, IsWhitelabel: cfg.IsWhitelabel}, time.Now),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/collaborators/add", emailRenderHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/payment/:uid/request", emailRenderHandler.Request, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/invoice/:uid/send", emailRenderHandler.Send, middleware.DjangoSessionAudit(sessionAuthentication))
	// oauth2_reset_password's own redis.Redis(..., db=2).
	resetOptions := *redisOptions
	resetOptions.DB = 2
	resetOptions.Dialer = redisclient.DialContext(&resetOptions)
	resetTokens := redis.NewClient(&resetOptions)
	defer resetTokens.Close()
	oauthAccountsHandler := handler.NewOAuthAccountsHandler(service.NewOAuthAccounts(repository.NewOAuthAccounts(db),
		repository.NewOAuthResetTokens(resetTokens), tasks, passwordcrypto.NewPasswordVerifier(passwordcrypto.DefaultPasswordLimits()), tokens,
		service.NewLoginResponses(service.NewLoginProfiles(repository.NewLoginProfiles(db), time.Now),
			service.NewAssociationProfiles(repository.NewAssociationProfiles(db), time.Now), repository.NewLoginResponseSettings(db),
			service.NewMembershipCardConfigurations(repository.NewMembershipCardConfigurations(db), time.Now), images,
			domain.RegistrationChoices{Family: cfg.FamilyRegistrationEnabled, Federation: cfg.FederationRegistrationEnabled, Multiple: cfg.MultipleRegistrationEnabled}),
		service.NewAssociationProfiles(repository.NewAssociationProfiles(db), time.Now),
		service.NewSocialClientIDs(repository.NewSocialConfiguration(db), cfg.GoogleClientID, cfg.AppleClientID),
		service.OAuthAccountSettings{AppURL: cfg.InstanceAdmin.AppURL, WhitelabelName: cfg.WhitelabelName, DefaultFromEmail: cfg.DefaultFromEmail,
			IsWhitelabel: cfg.IsWhitelabel, Debug: cfg.Debug}, time.Now),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/oauth2/signup", oauthAccountsHandler.Signup, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/oauth2/partial-signup", oauthAccountsHandler.PartialSignup, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/oauth2/reset", oauthAccountsHandler.Reset, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/oauth2/delete-account", oauthAccountsHandler.DeleteAccount, middleware.DjangoSessionAudit(sessionAuthentication))
	subscriptionWritesHandler := handler.NewSubscriptionWritesHandler(service.NewSubscriptionEdits(repository.NewSubscriptionEdits(db), time.Now, uuid.NewString),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/subscription/:uid/edit", subscriptionWritesHandler.Edit, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/course/locations/list", courseLocationListHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	courseLocationWritesHandler := handler.NewCourseLocationWritesHandler(service.NewCourseLocationWrites(repository.NewCourseLocationWrites(db), time.Now,
		uuid.NewString), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/course/locations/add", courseLocationWritesHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/course/locations/:pk/update", courseLocationWritesHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/course/locations/:pk/delete", courseLocationWritesHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	campsPeriodsHandler := handler.NewCampsPeriodsHandler(service.NewCampsPeriods(repository.NewCampsPeriods(db), time.Now, uuid.NewString),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/camps-and-retreats/periods/add", campsPeriodsHandler.AddPeriod, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/camps-and-retreats/periods/:uid/info", campsPeriodsHandler.PeriodInfo, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/camps-and-retreats/periods/:uid/update", campsPeriodsHandler.UpdatePeriod, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/camps-and-retreats/periods/:uid/delete", campsPeriodsHandler.DeletePeriod, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/camps-and-retreats/periods/services/add", campsPeriodsHandler.AddService, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/camps-and-retreats/periods/services/:uid/update", campsPeriodsHandler.UpdateService, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/camps-and-retreats/periods/services/:uid/delete", campsPeriodsHandler.DeleteService, middleware.DjangoSessionAudit(sessionAuthentication))
	campsCoreHandler := handler.NewCampsCoreHandler(service.NewCampsCore(repository.NewCampsPeriods(db), time.Now, uuid.NewString),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/camps-and-retreats/list", campsCoreHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/camps-and-retreats/add", campsCoreHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/camps-and-retreats/:uid/info", campsCoreHandler.Info, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/camps-and-retreats/:uid/update", campsCoreHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/camps-and-retreats/:uid/delete", campsCoreHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	campsSubscriptionsHandler := handler.NewCampsSubscriptionsHandler(service.NewCampsSubscriptions(repository.NewCampsPeriods(db), time.Now,
		uuid.NewString), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/camps-and-retreats/:uid/subscriptions/list", campsSubscriptionsHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/camps-and-retreats/:uid/subscriptions/add", campsSubscriptionsHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/camps-and-retreats/:uid/subscriptions/update", campsSubscriptionsHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/camps-and-retreats/:uid/subscriptions/delete", campsSubscriptionsHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	subscriptionMembershipsHandler := handler.NewSubscriptionMembershipsHandler(service.NewSubscriptionMemberships(repository.NewSubscriptionMemberships(db),
		time.Now, uuid.NewString), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/subscription/memberships/list", subscriptionMembershipsHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/memberships/add", subscriptionMembershipsHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/memberships/:pk/update", subscriptionMembershipsHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/memberships/:pk/delete", subscriptionMembershipsHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	associateDraftsHandler := handler.NewAssociateDraftsHandler(service.NewAssociateDrafts(repository.NewAssociateDrafts(db), time.Now, uuid.NewString),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/subscription/associates-draft/list", associateDraftsHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/associates-draft/add", associateDraftsHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/associates-draft/bulk-delete", associateDraftsHandler.BulkDelete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/associates-draft/:uid/edit", associateDraftsHandler.Edit, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/associates-draft/:uid/delete", associateDraftsHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	subscriptionMedicalHandler := handler.NewSubscriptionMedicalHandler(service.NewSubscriptionMedical(repository.NewSubscriptionMedical(db), time.Now,
		uuid.NewString), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/subscription/:uid/medical-appointments/list", subscriptionMedicalHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/:uid/medical-appointments/:medical_appointments_id/delete", subscriptionMedicalHandler.Delete,
		middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/:uid/medical-certificate/edit", subscriptionMedicalHandler.Edit, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/:uid/medical-certificate/set-certificate-expiration", subscriptionMedicalHandler.SetExpiration,
		middleware.DjangoSessionAudit(sessionAuthentication))
	courseSubscriptionsHandler := handler.NewCourseSubscriptionsHandler(service.NewCourseSubscriptions(repository.NewCourseSubscriptions(db), time.Now),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/course-subscriptions/list", courseSubscriptionsHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/course-subscriptions/bulk-delete", courseSubscriptionsHandler.BulkDelete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/course-subscriptions/:pk/update", courseSubscriptionsHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/course-subscriptions/:pk/delete", courseSubscriptionsHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	paymentStatesHandler := handler.NewPaymentStatesHandler(service.NewPaymentStates(repository.NewPaymentStates(db), time.Now).WithTasks(tasks),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/payment/:uid/approve", paymentStatesHandler.Approve, middleware.DjangoSessionAudit(sessionAuthentication))
	invoiceGenerateHandler := handler.NewInvoiceGenerateHandler(paymentStatesHandler,
		service.NewInvoiceGenerations(repository.NewInvoiceGenerations(repository.NewPaymentStates(db)), tasks, time.Now))
	e.Any("/payment/:uid/generate-invoice", invoiceGenerateHandler.Generate, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/payment/:uid/cancel", paymentStatesHandler.Cancel, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/payment/:uid/delete", paymentStatesHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	// stripe/*: STRIPE_API_BASE (default https://api.stripe.com) is stripe.api_base.
	stripeRoutesHandler := handler.NewStripeRoutesHandler(service.NewStripeRoutes(repository.NewStripeRoutes(db),
		repository.NewSocialConfiguration(db), service.StripeEnvironmentKeys{PublicKey: cfg.InstanceAdmin.StripePublicKey,
			SecretKey: cfg.InstanceAdmin.StripeKey, WebhookSecret: cfg.InstanceAdmin.StripeWebhookSecret}, cfg.SessionSecret,
		stripeapi.NewClient(os.Getenv("STRIPE_API_BASE")), time.Now), service.NewActorAuthentication(accounts, tokens), identities,
		middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/stripe/info", stripeRoutesHandler.Info, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/stripe/on-boarding", stripeRoutesHandler.OnBoarding, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/stripe/complete-on-boarding", stripeRoutesHandler.CompleteOnBoarding, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/stripe/pay/:payment_id", stripeRoutesHandler.Pay, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/stripe/multiple-pay", stripeRoutesHandler.MultiplePay, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/stripe/webhook", stripeRoutesHandler.Webhook, middleware.DjangoSessionAudit(sessionAuthentication))
	// google/*: GOOGLE_OAUTH2_BASE / GOOGLE_API_BASE (unset in production) redirect the Google origins.
	googleSettings, googleSecureCookie := service.GoogleCalendarSettingsFrom(os.Getenv)
	googleCalendarHandler := handler.NewGoogleCalendarHandler(service.NewGoogleCalendar(repository.NewGoogleCalendars(db, cache),
		googleapi.NewTransport(os.Getenv("GOOGLE_OAUTH2_BASE"), os.Getenv("GOOGLE_API_BASE"), mathrand.Float64), googleSettings, time.Now),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations,
		googleSecureCookie, time.Now)
	e.Any("/google/check", googleCalendarHandler.Check, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/google/oauth2callback", googleCalendarHandler.Callback, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/google/calendar/config", googleCalendarHandler.Config, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/google/calendar/list", googleCalendarHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/google/calendar/:course_id/export", googleCalendarHandler.Export, middleware.DjangoSessionAudit(sessionAuthentication))
	paymentReadsHandler := handler.NewPaymentReadsHandler(service.NewPaymentReads(repository.NewPaymentReads(db), time.Now,
		cfg.StripePublicKey != "" && cfg.StripeSecretConfigured), service.NewActorAuthentication(accounts, tokens), identities)
	e.Any("/payment/list", paymentReadsHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/payment/stats", paymentReadsHandler.Stats, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/:uid/payments", paymentReadsHandler.SubscriptionPayments, middleware.DjangoSessionAudit(sessionAuthentication))
	paymentExportHandler := handler.NewPaymentExportHandler(service.NewPaymentExport(repository.NewPaymentExport(db), time.Now),
		service.NewActorAuthentication(accounts, tokens), identities)
	e.Any("/payment/list/export", paymentExportHandler.Export, middleware.DjangoSessionAudit(sessionAuthentication))
	miscExportsHandler := handler.NewMiscExportsHandler(service.NewMiscExports(repository.NewMiscExports(db), tasks, time.Now),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/subscription/list/export", miscExportsHandler.SubscriptionExport, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/invoice/list/export", miscExportsHandler.InvoiceExport, middleware.DjangoSessionAudit(sessionAuthentication))
	subscriptionReadsHandler := handler.NewSubscriptionReadsHandler(service.NewSubscriptionReads(repository.NewSubscriptionReads(db),
		repository.NewCourseSubscriptions(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities)
	e.Any("/subscription/list", subscriptionReadsHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/list/all", subscriptionReadsHandler.ListAll, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/list/archived", subscriptionReadsHandler.Archived, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/:uid/info", subscriptionReadsHandler.Info, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/:uid/calendar", subscriptionReadsHandler.Calendar, middleware.DjangoSessionAudit(sessionAuthentication))
	subscriptionMiscHandler := handler.NewSubscriptionMiscHandler(service.NewSubscriptionMisc(repository.NewSubscriptionMisc(db),
		service.NewSubscriptionReads(repository.NewSubscriptionReads(db), repository.NewCourseSubscriptions(db), time.Now), time.Now,
		uuid.NewString), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/subscription/calculate-tax-code", subscriptionMiscHandler.TaxCode, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/import/status", subscriptionMiscHandler.ImportStatus, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/get-associations-for-federation", subscriptionMiscHandler.Federation, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/generate-token-link", subscriptionMiscHandler.GenerateLink, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/validate-token-link-and-get-subscriptions", subscriptionMiscHandler.ValidateLink,
		middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/:uid/delete-document/:subscription_file_id", subscriptionMiscHandler.DeleteDocument,
		middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/payment-bulk/archive", paymentStatesHandler.BulkArchive, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/payment-bulk/delete", paymentStatesHandler.BulkDelete, middleware.DjangoSessionAudit(sessionAuthentication))
	paymentWritesHandler := handler.NewPaymentWritesHandler(service.NewPaymentWrites(repository.NewPaymentWrites(repository.NewPaymentStates(db)),
		time.Now, uuid.NewString), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext,
		paymentCategoryOperations)
	e.Any("/payment/add", paymentWritesHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/payment/bulk-add", paymentWritesHandler.BulkAdd, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/payment/:uid/update", paymentWritesHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	paymentMiscHandler := handler.NewPaymentMiscHandler(service.NewPaymentMisc(repository.NewPaymentMisc(repository.NewPaymentStates(db)), time.Now,
		uuid.NewString, cfg.WhitelabelName), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext,
		paymentCategoryOperations)
	e.Any("/payment/simulation/partial-quotes", paymentMiscHandler.Simulation, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/payment/simulation/partial-quotes-apply", paymentMiscHandler.Apply, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/payment/sign", paymentMiscHandler.Sign, middleware.DjangoSessionAudit(sessionAuthentication))
	courseStateWritesHandler := handler.NewCourseStateWritesHandler(service.NewCourseStateWrites(repository.NewCourseStateWrites(db), time.Now),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/course/:uid/enable", courseStateWritesHandler.Enable, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/course/:uid/disable", courseStateWritesHandler.Disable, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/course/:uid/pin", courseStateWritesHandler.Pin, middleware.DjangoSessionAudit(sessionAuthentication))
	courseOverviewHandler := handler.NewCourseOverviewHandler(service.NewCourseOverviews(repository.NewCourseOverviews(db)),
		service.NewActorAuthentication(accounts, tokens), identities)
	e.Any("/course/:uid/overview", courseOverviewHandler.Overview, middleware.DjangoSessionAudit(sessionAuthentication))
	courseListHandler := handler.NewCourseListHandler(service.NewCourseLists(repository.NewCourseLists(db), time.Now),
		service.NewActorAuthentication(accounts, tokens), identities)
	e.Any("/course/list", courseListHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	courseCalendarHandler := handler.NewCourseCalendarHandler(service.NewCourseCalendars(repository.NewCourseCalendars(db), time.Now, uuid.NewString),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/course/:uid/calendar", courseCalendarHandler.Calendar, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/course/:uid/calendar/update", courseCalendarHandler.CalendarUpdate, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/calendar/events", courseCalendarHandler.Events, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/calendar/events/update", courseCalendarHandler.EventsUpdate, middleware.DjangoSessionAudit(sessionAuthentication))
	miscRoutesHandler := handler.NewMiscRoutesHandler(service.NewCalendarExport(repository.NewCourseCalendars(db), time.Now),
		service.NewCommunicationsPost(repository.NewMiscRoutes(db), time.Now, uuid.NewString),
		service.NewSubscriptionCard(repository.NewMiscRoutes(db), time.Now, uuid.NewString),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/calendar/events/export", miscRoutesHandler.CalendarExport, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/send/post", miscRoutesHandler.Post, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/:uid/card", miscRoutesHandler.Card, middleware.DjangoSessionAudit(sessionAuthentication))
	courseAttendanceHandler := handler.NewCourseAttendanceHandler(service.NewCourseAttendance(repository.NewCourseAttendance(db), time.Now),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/course/:uid/attendees", courseAttendanceHandler.Attendees, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/course/:uid/attendees/:attendance_day_uid/update", courseAttendanceHandler.AttendeesUpdate, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/:uid/attendance", courseAttendanceHandler.SubscriptionAttendance, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/attendance/:uid/mark", courseAttendanceHandler.Mark, middleware.DjangoSessionAudit(sessionAuthentication))
	courseWritesHandler := handler.NewCourseWritesHandler(service.NewCourseWrites(repository.NewCourseWrites(db), time.Now, uuid.NewString),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/course/add", courseWritesHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/course/:uid/update", courseWritesHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/course/:uid/delete", courseWritesHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/course/:uid/overview/:uid_subscription/update", courseWritesHandler.OverviewUpdate, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/course/:uid/overview/:uid_subscription/delete", courseWritesHandler.OverviewDelete, middleware.DjangoSessionAudit(sessionAuthentication))
	courseEnrolmentHandler := handler.NewCourseEnrolmentHandler(service.NewCourseEnrolment(repository.NewCourseEnrolment(db), time.Now, uuid.NewString),
		service.NewCourseSubscriptions(repository.NewCourseSubscriptions(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities,
		middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/course-subscriptions/add", courseEnrolmentHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/course/:uid/overview/:uid_subscription/add", courseEnrolmentHandler.OverviewAdd, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/course-installment/:uid/make-payment", courseEnrolmentHandler.MakePayment, middleware.DjangoSessionAudit(sessionAuthentication))
	modulesReadsHandler := handler.NewModulesReadsHandler(service.NewModulesReads(repository.NewModulesReads(db),
		service.NewAssociationProfiles(repository.NewAssociationProfiles(db), time.Now), time.Now),
		service.NewActorAuthentication(accounts, tokens), identities)
	e.Any("/modules/list", modulesReadsHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/modules/check-link", modulesReadsHandler.CheckLink, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/modules/:module_id/overview", modulesReadsHandler.Overview, middleware.DjangoSessionAudit(sessionAuthentication))
	modulesWritesHandler := handler.NewModulesWritesHandler(service.NewModulesWrites(repository.NewModulesWrites(db),
		service.NewAssociationProfiles(repository.NewAssociationProfiles(db), time.Now), time.Now, uuid.NewString),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/modules/add", modulesWritesHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/modules/:module_id/update", modulesWritesHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/modules/:module_id/duplicate", modulesWritesHandler.Duplicate, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/modules/:module_id/delete", modulesWritesHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	modulesResponsesHandler := composeModulesResponses(cfg, db, service.NewActorAuthentication(accounts, tokens), identities, paymentCategoryOperations)
	e.Any("/modules/:module_id/info", modulesResponsesHandler.Info, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/modules/:module_id/export", modulesResponsesHandler.Export, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/modules/:module_id/response/add", modulesResponsesHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/modules/response/:module_response_id/approve", modulesResponsesHandler.Approve, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/modules/response/:module_response_id/delete", modulesResponsesHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/modules/response/:module_response_id/add-attachment", modulesResponsesHandler.Attachment, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instructor/:uid/info", instructorListHandler.Info, middleware.DjangoSessionAudit(sessionAuthentication))
	instructorHoursHandler := handler.NewInstructorHoursHandler(service.NewInstructorHoursService(repository.NewInstructorHours(db), time.Now,
		uuid.NewString), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/instructor/:uid/hours/list", instructorHoursHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instructor/:uid/hours/add", instructorHoursHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instructor/:uid/hours/calculate", instructorHoursHandler.Calculate, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instructor/:uid/hours/:instructor_hours_id/update", instructorHoursHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instructor/:uid/hours/:instructor_hours_id/delete", instructorHoursHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	instructorWritesHandler := handler.NewInstructorWritesHandler(service.NewInstructorWrites(repository.NewInstructorWrites(db), time.Now,
		uuid.NewString), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations).
		WithReport(service.NewInstructorReport(repository.NewInstructorReport(db), time.Now))
	e.Any("/instructor/add", instructorWritesHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instructor/:uid/update", instructorWritesHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instructor/:uid/delete", instructorWritesHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instructor/:uid/hours/add/compensation", instructorWritesHandler.Compensation, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instructor/report", instructorWritesHandler.Report, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instructor/lessons-hours", instructorWritesHandler.Lessons, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instructor/:uid/lessons-hours", instructorWritesHandler.Lessons, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/audit-logs/list", auditLogHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/audit-logs/:id/detail", auditLogHandler.Detail, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/audit-logs/stats", auditSummaryHandler.Stats, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/audit-logs/models", auditSummaryHandler.Models, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/personas/list", personasHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/personas/all", personasHandler.All, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/personas/all-tutors", personasHandler.AllTutors, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/personas/all-with-subscriptions", personasHandler.AllWithSubscriptions, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/personas/:pk/info", personasHandler.Info, middleware.DjangoSessionAudit(sessionAuthentication))
	personasWritesHandler := handler.NewPersonasWritesHandler(personasService,
		service.NewPersonaWrites(repository.NewPersonaWrites(db), personasService, time.Now, uuid.NewString),
		service.NewPersonaSubscriptions(repository.NewPersonaSubscriptions(db), personasService),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	personasHandler.WithWrites(personasWritesHandler)
	e.Any("/personas/add", personasWritesHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/personas/bulk-delete", personasWritesHandler.BulkDelete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/personas/:pk/update", personasWritesHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/personas/:pk/delete", personasWritesHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/personas/:pk/recover", personasWritesHandler.Recover, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/personas-subscriptions/:pk/list", personasWritesHandler.Subscriptions, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/supplier/list", suppliersHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/supplier/:uid/info", suppliersHandler.Info, middleware.DjangoSessionAudit(sessionAuthentication))
	supplierWritesHandler := handler.NewSupplierWritesHandler(service.NewSupplierWrites(repository.NewSupplierWrites(db), time.Now, uuid.NewString),
		service.NewSupplierDeletes(repository.NewSupplierWrites(db), time.Now),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/supplier/:uid/delete", supplierWritesHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/supplier/bulk-delete", supplierWritesHandler.BulkDelete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/supplier/add", supplierWritesHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/supplier/:uid/update", supplierWritesHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/balance-sheet/archived", balanceAccountsHandler.Archived, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/balance-sheet/accounts/list", balanceAccountsHandler.Accounts, middleware.DjangoSessionAudit(sessionAuthentication))
	balanceAccountWritesHandler := handler.NewBalanceAccountWritesHandler(service.NewBalanceAccountWrites(repository.NewBalanceAccounts(db),
		service.NewBalanceAccounts(repository.NewBalanceAccounts(db)), time.Now, uuid.NewString), service.NewActorAuthentication(accounts, tokens),
		identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/balance-sheet/accounts/add", balanceAccountWritesHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/balance-sheet/accounts/:uid/update", balanceAccountWritesHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	balanceTransferWritesHandler := handler.NewBalanceTransferWritesHandler(service.NewBalanceTransferWrites(repository.NewBalanceAccounts(db),
		service.NewBalanceAccounts(repository.NewBalanceAccounts(db)), time.Now, uuid.NewString), service.NewActorAuthentication(accounts, tokens),
		identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/balance-sheet/accounts-transfer/add", balanceTransferWritesHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/balance-sheet/accounts-transfer/:uid/update", balanceTransferWritesHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/balance-sheet/accounts-transfer/:uid/delete", balanceTransferWritesHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/balance-sheet/accounts-transfer/list", balanceAccountsHandler.Transfers, middleware.DjangoSessionAudit(sessionAuthentication))
	balanceSheetHandler := handler.NewBalanceSheetHandler(service.NewBalanceSheetWrites(repository.NewBalanceAccounts(db),
		service.NewAssociationProfiles(repository.NewAssociationProfiles(db), time.Now), time.Now, uuid.NewString),
		service.NewBalanceAccountDeletes(repository.NewBalanceAccounts(db), service.NewBalanceAccounts(repository.NewBalanceAccounts(db)), time.Now),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/balance-sheet", balanceSheetHandler.Sheet, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/balance-sheet/accounts/:uid/delete", balanceSheetHandler.AccountDelete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/invoice-suppliers/stats", supplierInvoiceStatsHandler.Stats, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/invoice-customers/stats", customerInvoiceStatsHandler.Stats, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/invoice-suppliers/:uid/delete", invoiceDeletesHandler.Supplier, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/folders/list", foldersHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/documents/list", documentArchiveHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/attendance-day/:uid/delete", attendanceDaysHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/attendance-day/:uid/mark-absent", attendanceDaysHandler.MarkAbsent, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/saved-reports/list", savedReportsHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/saved-reports/add", savedReportsHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/saved-reports/:pk/info", savedReportsHandler.Info, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/saved-reports/:pk/update", savedReportsHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/saved-reports/:pk/delete", savedReportsHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/saved-reports/:pk/run", savedReportsHandler.Run, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/documents/bulk-delete", documentArchiveHandler.BulkDelete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/documents/add", documentUploadsHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/documents/:pk/update", documentUploadsHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/:uid/upload-document", documentUploadsHandler.UploadDocument, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/:uid/medical-certificate/upload", documentUploadsHandler.MedicalUpload, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/document/medical/certificate/", documentUploadsHandler.CertificateDocument, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/documents/:pk/move", documentArchiveHandler.Move, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/documents/:pk/delete", documentArchiveHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/document/subscription/:uid", documentRenderHandler.Subscription, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/document/invoice/:uid", documentRenderHandler.Invoice, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/document/einvoice/:uid", documentRenderHandler.Einvoice, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/document/compensation/:uid", documentRenderHandler.Compensation, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/document/template/:uid", documentRenderHandler.Template, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/document/medical-appointment/:uid", documentRenderHandler.MedicalAppointment, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/document/subscription/preview/", documentViewsHandler.Preview, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/document/subscription/:uid/view/", documentViewsHandler.Subscription, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/document/compensation/:uid/view/", documentViewsHandler.Compensation, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/document/template/:uid/view/", documentViewsHandler.Template, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/document/invoice/:uid/view/", invoiceViewsHandler.Invoice, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/document/einvoice/:uid/view/", invoiceViewsHandler.Einvoice, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/document/medical-appointment/:uid/view/", documentViewsHandler.MedicalAppointment, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/document/retrieve/:uid", docmanagerReadsHandler.Retrieve, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/document/:uid/delete", docmanagerReadsHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/folders/:pk/delete", foldersHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/folders/:pk/move", foldersHandler.Move, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/folders/add", folderWritesHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/folders/:pk/update", folderWritesHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/invoice-customers/:uid/delete", invoiceDeletesHandler.Customer, middleware.DjangoSessionAudit(sessionAuthentication))
	invoicePartiesHandler := handler.NewInvoicePartiesHandler(service.NewInvoiceParties(repository.NewInvoiceParties(db), time.Now, uuid.NewString),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/invoice-customers/list", invoicePartiesHandler.CustomerList, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/invoice-customers/add", invoicePartiesHandler.CustomerAdd, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/invoice-customers/:uid/update", invoicePartiesHandler.CustomerUpdate, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/invoice-suppliers/list", invoicePartiesHandler.SupplierList, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/invoice-suppliers/add", invoicePartiesHandler.SupplierAdd, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/invoice-suppliers/:uid/update", invoicePartiesHandler.SupplierUpdate, middleware.DjangoSessionAudit(sessionAuthentication))
	invoiceCoreHandler := handler.NewInvoiceCoreHandler(service.NewInvoiceCore(repository.NewInvoiceCore(db), time.Now),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/invoice/list", invoiceCoreHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/invoice/list/archived", invoiceCoreHandler.Archived, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/invoice/:uid/delete", invoiceCoreHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/invoice-bulk/archive", invoiceCoreHandler.BulkArchive, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/invoice-bulk/delete", invoiceCoreHandler.BulkDelete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/sport-associations/:uid/admin-update", associationAdminHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/templates/list", moduleTemplatesHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/templates/add", moduleTemplatesHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/templates/bulk-delete", moduleTemplatesHandler.BulkDelete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/templates/:pk/delete", moduleTemplatesHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/templates/:pk/update", moduleTemplatesHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/workflows/:workflow_id/delete", workflowWritesHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/workflows/:workflow_id/update", workflowWritesHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/workflows/list", workflowReadsHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/workflows/:workflow_id/details", workflowReadsHandler.Details, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/email-logs/list", emailLogsHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/settings/smtp/info", communicationSMTPHandler.Info, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/settings/smtp/update", communicationSMTPHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/settings/smtp/verify", communicationSMTPHandler.Verify, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/staff-board/list", staffBoardHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/staff-board/add", staffBoardHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/staff-board/:staff_board_message_id/update", staffBoardHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/staff-board/:staff_board_message_id/delete", staffBoardHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/healthz", healthHandler.Health, middleware.DjangoCSRF(cfg.CSRFTrustedOrigins))
	e.Any("/readyz", healthHandler.Readiness, middleware.DjangoCSRF(cfg.CSRFTrustedOrigins))
	e.Any("/oauth2/login", loginHandler.Login, middleware.DjangoSessionAudit(sessionAuthentication), middleware.LoginRateLimit(cfg.RateLimitEnabled, loginRate))
	e.Any("/oauth2/refresh-token", refreshHandler.Refresh, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/oauth2/check/email", identifierHandler.Email, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/oauth2/check/username", identifierHandler.Username, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instance/status", instanceHandler.Status, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instance/config", instanceHandler.Config, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instance/manifest.json", instanceManifestHandler.Read, handler.ManualResponseEncoding(), middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/schema/", apiSchemaHandler.Schema, handler.ManualResponseEncoding(), middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/schema/swagger-ui/", apiSchemaHandler.Swagger, handler.ManualResponseEncoding(), middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/schema/redoc/", apiSchemaHandler.Redoc, handler.ManualResponseEncoding(), middleware.DjangoSessionAudit(sessionAuthentication))
	for _, path := range []string{"/schema", "/schema/swagger-ui", "/schema/redoc"} {
		e.Any(path, apiSchemaHandler.AppendSlash, middleware.DjangoSessionAudit(sessionAuthentication))
	}
	e.Any("/instance/logo.png", instanceLogoHandler.Read, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instance/access", instanceHandler.Access, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instance/admin", instanceAdminHandler.Admin, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instance/admin/email", instanceAdminHandler.Email, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instance/admin/integrations/:provider", instanceAdminHandler.Integration, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instance/admin/integrations/:provider/test", instanceOutboundHandler.IntegrationTest, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instance/admin/email/test", instanceOutboundHandler.EmailTest, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instance/admin/releases", instanceOutboundHandler.Releases, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instance/admin/diagnostics", instanceAdminHandler.Diagnostics, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instance/admin/updates", instanceAdminHandler.Updates, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instance/admin/restarts", instanceAdminHandler.Restarts, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instance/admin/bakney-pairing", bakneySSOHandler.Admin, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/bakney/v1/pairing/challenge", bakneySSOHandler.Challenge, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/bakney/v1/pairing/commit", bakneySSOHandler.Commit, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/bakney/v1/login-start", bakneySSOHandler.LoginStart, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/bakney/v1/callback", bakneySSOHandler.Callback, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/bakney/v1/session", bakneySSOHandler.Session, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instance/setup-token/validate", instanceSetupHandler.Validate, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instance/configure", instanceSetupHandler.Configure, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instance/reconfigure", instanceSetupHandler.Reconfigure, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instance/logo", instanceSetupHandler.Logo, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instance/admin/logo", instanceSetupHandler.OwnerLogo, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/manuale/sections", manualHandler.Sections, handler.ManualResponseEncoding(), middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/manuale/assets/*", manualHandler.Asset, handler.ManualResponseEncoding(), middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/profile/info", profileHandler.Info, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/profile/update/password", profilePasswordHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/profile/update", profileUpdateHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/profile/settings/tables", profileTablesHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/profile/settings", profileSettingsHandler.Settings, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/profile/integrations", profileSettingsHandler.Integrations, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/profile/associates/course/:uid", profileMiscHandler.Course, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/profile/associates/sport-association/:uid", profileMiscHandler.SportAssociation, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/profile/image/:uid", profileMiscHandler.Image, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/onboarding/update", profileMiscHandler.Onboarding, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/association/export/active", associationTransferHandler.ExportActive, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/association/export/list", associationTransferHandler.ExportList, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/association/export/status", associationTransferHandler.ExportStatus, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/association/export/delete", associationTransferHandler.ExportDelete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/association/export/start", associationTransferHandler.ExportStart, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/association/import/status", associationTransferHandler.ImportStatus, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/association/import/validate", associationTransferHandler.ImportValidate, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/association/import/start", associationTransferHandler.ImportStart, middleware.DjangoSessionAudit(sessionAuthentication))
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
	// Channels WebSocket routes (core.asgi): Django's notification store is
	// Redis database 0; group messages arrive through Django's mirrored layer.
	storeOptions := *redisOptions
	storeOptions.DB = 0
	storeOptions.Dialer = redisclient.DialContext(&storeOptions)
	notificationRedis := redis.NewClient(&storeOptions)
	defer notificationRedis.Close()
	notificationStore, err := repository.NewNotificationStore(db, notificationRedis, cache)
	if err != nil {
		return err
	}
	// subscription/add: Channels group messages are written for Django sockets
	// and mirrored for Go sockets (internal/channels), on Redis database 0.
	subscriptionNotifier := service.NewSubscriptionNotifier(repository.NewSubscriptionNotifications(db), notificationStore,
		channels.NewLayer(notificationRedis, nil, nil, nil), time.Now)
	subscriptionCreates := service.NewSubscriptionCreates(repository.NewSubscriptionCreates(db), tasks, subscriptionNotifier, time.Now, nil)
	subscriptionCreateHandler := handler.NewSubscriptionCreateHandler(subscriptionCreates, service.NewActorAuthentication(accounts, tokens),
		identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/subscription/add", subscriptionCreateHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	// subscription/renew and associates-draft approve run create_subscription (subscription-renew.md).
	subscriptionRenewHandler := handler.NewSubscriptionRenewHandler(
		service.NewSubscriptionRenewals(repository.NewSubscriptionRenewals(db), subscriptionCreates),
		service.NewSubscriptionDraftApprovals(repository.NewSubscriptionDraftApprovals(db), subscriptionCreates),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/subscription/renew", subscriptionRenewHandler.Renew, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/subscription/associates-draft/list/approve", subscriptionRenewHandler.Approve, middleware.DjangoSessionAudit(sessionAuthentication))
	// subscription/<uid>/transfer: the recipient's notification through the same notifier.
	subscriptionTransferHandler := handler.NewSubscriptionTransferHandler(service.NewSubscriptionTransfers(repository.NewSubscriptionTransfers(db),
		subscriptionNotifier, tasks, passwordcrypto.NewPasswordVerifier(passwordcrypto.DefaultPasswordLimits()),
		service.OAuthAccountSettings{AppURL: cfg.InstanceAdmin.AppURL, WhitelabelName: cfg.WhitelabelName, DefaultFromEmail: cfg.DefaultFromEmail,
			IsWhitelabel: cfg.IsWhitelabel, Debug: cfg.Debug}, time.Now),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/subscription/:uid/transfer", subscriptionTransferHandler.Transfer, middleware.DjangoSessionAudit(sessionAuthentication))
	// subscription/sign: signature upload, then subscription_signed and the print task after commit.
	subscriptionSignHandler := composeSubscriptionSign(cfg, db, tasks, service.NewActorAuthentication(accounts, tokens), identities,
		middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/subscription/sign", subscriptionSignHandler.Sign, middleware.DjangoSessionAudit(sessionAuthentication))
	realtimeHub := service.NewRealtimeHub()
	pubsubOptions := storeOptions
	pubsubOptions.ReadTimeout = -1
	pubsubOptions.Dialer = redisclient.DialContext(&pubsubOptions)
	realtimeRedis := redis.NewClient(&pubsubOptions)
	defer realtimeRedis.Close()
	stopRealtime := runRealtimeSubscription(realtimeRedis, realtimeHub)
	defer stopRealtime()
	webSockets := handler.NewWebSocketHandler(service.NewWebSocketIdentities(accounts, tokens, identities),
		service.NewNotificationManager(notificationStore), realtimeHub, cfg.Version)
	server := &http.Server{Addr: cfg.ListenAddress, Handler: webSockets.Routes(e), ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 60 * time.Second}
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

// runRealtimeSubscription subscribes before the server starts (waiting up to
// five seconds) and keeps resubscribing in the background after failures.
func runRealtimeSubscription(client *redis.Client, hub *service.RealtimeHub) func() {
	ctx, cancel := context.WithCancel(context.Background())
	subscribe := func() *repository.RealtimeSubscription {
		attempt, cancelAttempt := context.WithTimeout(ctx, 5*time.Second)
		defer cancelAttempt()
		subscription, err := repository.SubscribeRealtime(attempt, client)
		if err != nil {
			slog.Warn("realtime group messages unavailable", "error", err)
			return nil
		}
		return subscription
	}
	var current *repository.RealtimeSubscription
	var mu sync.Mutex
	first := subscribe()
	go func() {
		subscription := first
		for ctx.Err() == nil {
			if subscription == nil {
				select {
				case <-ctx.Done():
					return
				case <-time.After(time.Second):
				}
				subscription = subscribe()
				continue
			}
			mu.Lock()
			current = subscription
			mu.Unlock()
			subscription.Run(hub.Deliver)
			subscription = nil
		}
	}()
	return func() {
		cancel()
		mu.Lock()
		defer mu.Unlock()
		if current != nil {
			current.Close()
		}
	}
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
