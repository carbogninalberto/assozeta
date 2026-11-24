package main

import (
	"context"
	"database/sql"
	"errors"
	"net"
	"os"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/instanceprobe"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentobjects"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

// composeInstanceOutbound builds the release feed, e-mail test, integration
// test and diagnostics routes on the administration service. Outbound
// requests use the process proxy environment and SSL_CERT_FILE (see
// instanceprobe).
//
// The diagnostics 'api' probe checks this server's own /healthz, as Django's
// checks its own process (http://127.0.0.1:8000, the port both API containers
// listen on): http://127.0.0.1:<GO_LISTEN_ADDR port>, or
// ASSOZETA_DIAGNOSTICS_API_URL when set.
func composeInstanceOutbound(cfg config.Config, db *sqlx.DB, cache, broker *redis.Client, admin *handler.InstanceAdminHandler,
	adminService *service.InstanceAdmin) (*handler.InstanceOutboundHandler, error) {
	client, err := instanceprobe.NewClient()
	if err != nil {
		return nil, err
	}
	releases, err := repository.NewReleaseCache(cache)
	if err != nil {
		return nil, err
	}
	api := os.Getenv("ASSOZETA_DIAGNOSTICS_API_URL")
	if api == "" {
		_, port, err := net.SplitHostPort(cfg.ListenAddress)
		if err != nil {
			return nil, err
		}
		api = "http://" + net.JoinHostPort("127.0.0.1", port)
	}
	storage := cfg.Storage
	probes := diagnosticProbes{db: db, cache: cache, broker: broker, client: client, storage: documentobjects.ProbeOptions{
		Endpoint: storage.Endpoint, AccessKey: storage.AccessKey, SecretKey: storage.SecretKey, Region: storage.Region,
		Bucket: storage.Bucket, Location: storage.Location}}
	outbound := service.NewInstanceOutbound(adminService, repository.NewInstanceAdmin(db), releases, service.SMTPTLS{Client: client}, time.Now).
		WithDiagnostics(probes, service.InstanceDiagnosticsSettings{APIURL: api, RendererHost: os.Getenv("PUPPETEER_HOST"),
			RendererPort: os.Getenv("PUPPETEER_PORT")})
	return handler.NewInstanceOutboundHandler(admin, outbound), nil
}

// schedulerHeartbeatKey is cache.make_key('instance:scheduler:heartbeat')
// (instance.scheduler.InstanceScheduler.publish_heartbeat).
const schedulerHeartbeatKey = ":1:instance:scheduler:heartbeat"

// diagnosticProbes are the connections of the diagnostics probes: the
// database, Django's cache (DB 1), the Celery broker (DB 0), the outbound
// HTTP client and the S3 storage settings.
type diagnosticProbes struct {
	db            *sqlx.DB
	cache, broker *redis.Client
	client        *instanceprobe.Client
	storage       documentobjects.ProbeOptions
}

func (p diagnosticProbes) Database(ctx context.Context) error {
	var one int
	if err := p.db.GetContext(ctx, &one, `SELECT 1`); err != nil {
		return err
	}
	if one != 1 {
		return errors.New("SELECT 1 answered another value")
	}
	return nil
}

func (p diagnosticProbes) Health(ctx context.Context, target string, expected *string) (bool, error) {
	return p.client.Health(ctx, target, expected)
}

func (p diagnosticProbes) Storage(ctx context.Context) error {
	return documentobjects.ListProbe(ctx, p.storage)
}

func (p diagnosticProbes) Ping(ctx context.Context) ([]any, error) {
	broadcast, err := celery.Inspect(ctx, p.broker, "ping", 2*time.Second, nil)
	if err != nil {
		return nil, err
	}
	return broadcast.Replies, nil
}

func (p diagnosticProbes) Heartbeat(ctx context.Context) ([]byte, bool, error) {
	raw, err := p.cache.Get(ctx, schedulerHeartbeatKey).Bytes()
	if errors.Is(err, redis.Nil) {
		return nil, false, nil
	}
	return raw, err == nil, err
}

// GoProgress reports whether both worker roles or the scheduler lease are fresh.
// Legacy databases and a Django authority use the broker/cache probes.
func (p diagnosticProbes) GoProgress(ctx context.Context, role string) (fresh, applicable bool, err error) {
	var query string
	switch role {
	case "worker":
		query = `SELECT (SELECT count(DISTINCT role) = 2 FROM public.assozeta_handoff_consumers
                    WHERE runtime = owner.runtime AND role IN ('broker','durable')
                      AND heartbeat_at BETWEEN now() - interval '90 seconds' AND now())
                  FROM public.assozeta_go_job_ownership owner WHERE singleton = 1 AND runtime = 'go'`
	case "scheduler":
		query = `SELECT EXISTS (SELECT FROM public.assozeta_scheduler_lease
                    WHERE runtime = owner.runtime AND ownership_generation = owner.generation
                      AND lease_until > now() AND heartbeat_at BETWEEN now() - interval '90 seconds' AND now())
                  FROM public.assozeta_go_job_ownership owner WHERE singleton = 1 AND runtime = 'go'`
	default:
		return false, false, errors.New("unknown runtime diagnostic")
	}
	err = p.db.GetContext(ctx, &fresh, query)
	var pgErr *pgconn.PgError
	if errors.Is(err, sql.ErrNoRows) || (errors.As(err, &pgErr) && pgErr.Code == "42P01") {
		return false, false, nil
	}
	return fresh, true, err
}
