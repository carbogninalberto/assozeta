// relay-go-realtime is the Go port of Django's relay_go_realtime management
// command (BE/notifications/management/commands/relay_go_realtime.py and
// notifications.go_realtime.relay_once): it claims pending rows of
// public.assozeta_go_realtime_events and sends them through the Channels
// layer (internal/channels: the channels_redis group_send plus the Go
// mirror), as Django's configured GoMirroredRedisChannelLayer does.
//
// Options: --once (process pending events once, print the counts and exit),
// --poll-seconds SECONDS (default 2.0; "--name value" or "--name=value") and
// --env-file PATH. Django's global management options and argparse prefix
// abbreviations are not accepted.
package main

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/channels"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	ownedpgx "github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/redisclient"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/jackc/pgx/v5"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

func main() { os.Exit(run(os.Args[1:], os.Stdout, os.Stderr)) }

const usage = "usage: relay-go-realtime [--once] [--poll-seconds POLL_SECONDS] [--env-file PATH]"

type options struct {
	once    bool
	poll    float64
	envFile string
}

func parse(args []string) (options, error) {
	parsed := options{poll: 2.0}
	for index := 0; index < len(args); index++ {
		name, value, inline := strings.Cut(args[index], "=")
		switch name {
		case "--once":
			if inline {
				return parsed, fmt.Errorf("argument --once: ignored explicit argument '%s'", value)
			}
			parsed.once = true
			continue
		case "--poll-seconds", "--env-file":
		default:
			return parsed, fmt.Errorf("unrecognized arguments: %s", args[index])
		}
		if !inline {
			if index+1 >= len(args) || strings.HasPrefix(args[index+1], "-") && !negativeNumber(args[index+1]) {
				return parsed, fmt.Errorf("argument %s: expected one argument", name)
			}
			index++
			value = args[index]
		}
		if name == "--env-file" {
			parsed.envFile = value
			continue
		}
		// float(value); select() refuses negative and non-finite timeouts,
		// so those are refused here before any work.
		seconds, err := strconv.ParseFloat(strings.TrimSpace(value), 64)
		if err != nil || math.IsNaN(seconds) || math.IsInf(seconds, 0) || seconds < 0 {
			return parsed, fmt.Errorf("argument --poll-seconds: invalid float value: '%s'", value)
		}
		parsed.poll = seconds
	}
	return parsed, nil
}

// negativeNumber is argparse's check for a value that looks like a negative number.
func negativeNumber(value string) bool {
	if len(value) < 2 || value[0] != '-' {
		return false
	}
	next := value[1]
	if next == '.' && len(value) > 2 {
		next = value[2]
	}
	return next >= '0' && next <= '9'
}

func run(args []string, stdout, stderr io.Writer) int {
	parsed, err := parse(args)
	if err != nil {
		fmt.Fprintln(stderr, usage)
		fmt.Fprintln(stderr, "relay-go-realtime: error: "+err.Error())
		return 2
	}
	cfg, err := config.Load(parsed.envFile)
	if err != nil {
		fmt.Fprintln(stderr, "invalid environment configuration")
		return 1
	}
	database, err := pgx.ParseConfig(cfg.DatabaseURL())
	if err != nil {
		fmt.Fprintln(stderr, "invalid database configuration")
		return 1
	}
	database.DefaultQueryExecMode = pgx.QueryExecModeExec
	database.ConnectTimeout = 5 * time.Second
	database.RuntimeParams["application_name"] = "assozeta-go-relay-realtime"
	db := sqlx.NewDb(ownedpgx.OpenDB(*database), "pgx")
	defer db.Close()
	client, err := channelLayerClient(cfg)
	if err != nil {
		fmt.Fprintln(stderr, err.Error())
		return 1
	}
	defer client.Close()
	logger := slog.New(slog.NewTextHandler(stderr, nil))
	relay := service.NewRelayGoRealtime(repository.NewRelayGoRealtimeOutbox(db),
		channels.NewLayer(client, nil, nil, logger), logger)
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	if parsed.once {
		counts, err := relay.Once(ctx)
		if err != nil {
			fmt.Fprintln(stderr, "relay-go-realtime failed")
			return 1
		}
		fmt.Fprintln(stdout, counts.String())
		return 0
	}
	wake, listenErr, err := listen(ctx, database)
	if err != nil {
		fmt.Fprintln(stderr, "relay-go-realtime failed")
		return 1
	}
	loopCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	go func() {
		// A lost LISTEN connection ends the command, as raw.poll() raises in Django.
		if err := <-listenErr; err != nil {
			cancel()
		}
	}()
	err = relay.Run(loopCtx, time.Duration(parsed.poll*float64(time.Second)), wake)
	if err != nil && !(errors.Is(err, context.Canceled) && ctx.Err() != nil) {
		fmt.Fprintln(stderr, "relay-go-realtime failed")
		return 1
	}
	return 0
}

// channelLayerClient is CHANNEL_LAYERS' single host: REDIS_HOST:REDIS_PORT
// database 0 with REDIS_PASSWORD (no username), rediss under REDIS_SSLAUTH.
func channelLayerClient(cfg config.Config) (*redis.Client, error) {
	options := &redis.Options{Addr: cfg.RedisAddress, Password: cfg.RedisPassword, DB: 0, MaxRetries: -1,
		DialTimeout: 2 * time.Second, ReadTimeout: 2 * time.Second, WriteTimeout: 2 * time.Second, ContextTimeoutEnabled: true}
	if cfg.RedisTLS {
		options.TLSConfig = &tls.Config{MinVersion: tls.VersionTLS12}
		if cfg.RedisRootCertificate != "" {
			pem, err := os.ReadFile(cfg.RedisRootCertificate)
			if err != nil {
				return nil, errors.New("cannot read Redis root certificate")
			}
			roots, err := x509.SystemCertPool()
			if err != nil {
				roots = x509.NewCertPool()
			}
			if !roots.AppendCertsFromPEM(pem) {
				return nil, errors.New("invalid Redis root certificate")
			}
			options.TLSConfig.RootCAs = roots
		}
	}
	options.Dialer = redisclient.DialContext(options)
	return redis.NewClient(options), nil
}

// listen opens a dedicated connection with LISTEN assozeta_go_realtime and
// signals wake (coalesced) on every notification.
func listen(ctx context.Context, database *pgx.ConnConfig) (<-chan struct{}, <-chan error, error) {
	conn, err := pgx.ConnectConfig(ctx, database)
	if err != nil {
		return nil, nil, err
	}
	if _, err := conn.Exec(ctx, "LISTEN assozeta_go_realtime"); err != nil {
		conn.Close(context.Background())
		return nil, nil, err
	}
	wake := make(chan struct{}, 1)
	failed := make(chan error, 1)
	go func() {
		defer conn.Close(context.Background())
		for {
			if _, err := conn.WaitForNotification(ctx); err != nil {
				if ctx.Err() != nil {
					failed <- nil
				} else {
					failed <- err
				}
				return
			}
			select {
			case wake <- struct{}{}:
			default:
			}
		}
	}()
	return wake, failed, nil
}
