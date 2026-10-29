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
	workflowWritesHandler := handler.NewAutomationWorkflowWritesHandler(service.NewAutomationWorkflowWrites(repository.NewAutomationWorkflows(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	workflowReadsHandler := handler.NewAutomationWorkflowReadsHandler(service.NewAutomationWorkflowReads(repository.NewAutomationWorkflows(db)), service.NewActorAuthentication(accounts, tokens), identities)
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
	auditSummaryHandler := handler.NewAuditSummaryHandler(service.NewAuditSummaries(repository.NewAuditSummaries(db)), service.NewActorAuthentication(accounts, tokens), identities)
	suppliersHandler := handler.NewSuppliersHandler(service.NewSuppliers(repository.NewSuppliers(db)), service.NewActorAuthentication(accounts, tokens), identities)
	carnetListHandler := handler.NewCarnetListHandler(service.NewCarnetLists(repository.NewCarnetList(db), time.Now), service.NewCarnetInfos(repository.NewCarnetList(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities)
	inconsistenciesHandler := handler.NewInconsistenciesHandler(service.NewInconsistencies(repository.NewInconsistencies(db), time.Now, time.Local), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	supplierInvoiceStatsHandler := handler.NewSupplierInvoiceStatsHandler(service.NewSupplierInvoiceStats(repository.NewSupplierInvoiceStats(db), time.Now, time.Local), service.NewActorAuthentication(accounts, tokens), identities)
	customerInvoiceStatsHandler := handler.NewCustomerInvoiceStatsHandler(service.NewCustomerInvoiceStats(repository.NewSupplierInvoiceStats(db), time.Now, time.Local), service.NewActorAuthentication(accounts, tokens), identities)
	invoiceDeletesHandler := handler.NewInvoiceDeletesHandler(service.NewInvoiceDeletes(repository.NewInvoiceDeletes(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	foldersHandler := handler.NewFoldersHandler(service.NewFolders(repository.NewFolders(db)), service.NewFolderDeletes(repository.NewFolders(db)), service.NewFolderMoves(repository.NewFolders(db)), service.NewActorAuthentication(accounts, tokens), identities, paymentCategoryOperations)
	documentArchiveHandler := handler.NewDocumentArchiveHandler(service.NewDocumentArchive(repository.NewDocumentArchive(db)), service.NewArchiveWrites(repository.NewDocumentArchive(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	savedReportsHandler := handler.NewSavedReportsHandler(service.NewSavedReports(repository.NewSavedReports(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities, paymentCategoryOperations)
	courseLocationListHandler := handler.NewCourseLocationListHandler(service.NewCourseLocationLists(repository.NewSearchProfiles(db)), service.NewActorAuthentication(accounts, tokens), identities)
	courseTagListHandler := handler.NewCourseTagListHandler(service.NewCourseTagLists(repository.NewSearchProfiles(db)), service.NewActorAuthentication(accounts, tokens), identities)
	instructorListHandler := handler.NewInstructorListHandler(service.NewInstructorLists(repository.NewInstructorLists(db)), service.NewInstructorInfos(repository.NewInstructorLists(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities)
	carnetAssigns := service.NewCarnetAssigns(repository.NewCarnetWrites(db), time.Now, uuid.NewString)
	carnetWritesHandler := handler.NewCarnetWritesHandler(service.NewCarnetAdds(repository.NewCarnetWrites(db), carnetAssigns, time.Now, uuid.NewString), service.NewCarnetReplaces(repository.NewCarnetWrites(db), carnetAssigns, time.Now), carnetAssigns, service.NewCarnetTopups(repository.NewCarnetWrites(db), time.Now, uuid.NewString), service.NewCarnetMetas(repository.NewCarnetWrites(db), time.Now), service.NewCarnetLinks(repository.NewCarnetWrites(db)), service.NewCarnetToggles(repository.NewCarnetWrites(db), time.Now), service.NewCarnetDeletes(repository.NewCarnetWrites(db), time.Now), service.NewCarnetUpdates(repository.NewCarnetWrites(db), time.Now), service.NewCarnetUnassigns(repository.NewCarnetWrites(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	paymentArchiveHandler := handler.NewPaymentArchiveHandler(service.NewPaymentArchives(repository.NewPaymentArchives(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	attendanceDaysHandler := handler.NewAttendanceDaysHandler(service.NewAttendanceDays(repository.NewAttendanceDays(db), time.Now), service.NewAttendanceMarks(repository.NewAttendanceDays(db), time.Now), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	emailLogsHandler := handler.NewEmailLogsHandler(service.NewEmailLogs(repository.NewEmailLogs(db), time.Now, time.Local), service.NewActorAuthentication(accounts, tokens), identities)
	staffBoardHandler := handler.NewStaffBoardHandler(service.NewStaffBoard(repository.NewStaffBoard(db), time.Now),
		service.NewActorAuthentication(accounts, tokens), identities, paymentCategoryOperations)
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
	e.Any("/communications/workflows/add", workflowWritesHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/testimonials/add", testimonialsHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/testimonials/update", testimonialsHandler.Webhook, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/statistic/dashboard/layout", dashboardLayoutHandler.Set, middleware.DjangoSessionAudit(sessionAuthentication))
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
	e.Any("/course/locations/list", courseLocationListHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	courseLocationWritesHandler := handler.NewCourseLocationWritesHandler(service.NewCourseLocationWrites(repository.NewCourseLocationWrites(db), time.Now,
		uuid.NewString), service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/course/locations/add", courseLocationWritesHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/course/locations/:pk/update", courseLocationWritesHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/course/locations/:pk/delete", courseLocationWritesHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	courseStateWritesHandler := handler.NewCourseStateWritesHandler(service.NewCourseStateWrites(repository.NewCourseStateWrites(db), time.Now),
		service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext, paymentCategoryOperations)
	e.Any("/course/:uid/enable", courseStateWritesHandler.Enable, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/course/:uid/disable", courseStateWritesHandler.Disable, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/course/:uid/pin", courseStateWritesHandler.Pin, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/instructor/:uid/info", instructorListHandler.Info, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/audit-logs/stats", auditSummaryHandler.Stats, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/audit-logs/models", auditSummaryHandler.Models, middleware.DjangoSessionAudit(sessionAuthentication))
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
	e.Any("/documents/bulk-delete", documentArchiveHandler.BulkDelete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/documents/:pk/move", documentArchiveHandler.Move, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/documents/:pk/delete", documentArchiveHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/folders/:pk/delete", foldersHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/folders/:pk/move", foldersHandler.Move, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/invoice-customers/:uid/delete", invoiceDeletesHandler.Customer, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/sport-associations/:uid/admin-update", associationAdminHandler.Update, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/templates/list", moduleTemplatesHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/templates/add", moduleTemplatesHandler.Add, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/templates/bulk-delete", moduleTemplatesHandler.BulkDelete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/templates/:pk/delete", moduleTemplatesHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/workflows/:workflow_id/delete", workflowWritesHandler.Delete, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/workflows/list", workflowReadsHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/workflows/:workflow_id/details", workflowReadsHandler.Details, middleware.DjangoSessionAudit(sessionAuthentication))
	e.Any("/communications/email-logs/list", emailLogsHandler.List, middleware.DjangoSessionAudit(sessionAuthentication))
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
