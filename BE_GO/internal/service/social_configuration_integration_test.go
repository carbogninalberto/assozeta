//go:build socialconfigintegration

package service

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

type socialConfigQueries struct{ count atomic.Int64 }

func (q *socialConfigQueries) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	if strings.HasPrefix(strings.ToUpper(strings.TrimSpace(d.SQL)), "SELECT") {
		q.count.Add(1)
	}
	return ctx
}
func (*socialConfigQueries) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

func socialConfigDatabase(t *testing.T) (*sqlx.DB, *socialConfigQueries, *SocialClientIDs) {
	t.Helper()
	if os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN") == "" {
		t.Fatal("run accounts adapter")
	}
	cfg, err := pgx.ParseConfig(os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN"))
	if err != nil {
		t.Fatal("invalid private database settings")
	}
	queries := &socialConfigQueries{}
	cfg.Tracer = queries
	db := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	db.SetMaxOpenConns(4)
	t.Cleanup(func() { db.Close() })
	return db, queries, NewSocialClientIDs(repository.NewSocialConfiguration(db), "env-google", "env-apple")
}

func TestSocialConfigurationAbsentPostgres(t *testing.T) {
	db, queries, s := socialConfigDatabase(t)
	for provider, want := range map[string]string{"google": "env-google", "apple": "env-apple"} {
		queries.count.Store(0)
		got, err := s.ClientID(context.Background(), provider)
		if err != nil || got != want || queries.count.Load() != 1 || db.Stats().InUse != 0 {
			t.Fatal("absent instance contract", got, err, queries.count.Load())
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	queries.count.Store(0)
	if got, err := s.ClientID(ctx, "google"); got != nil || !errors.Is(err, context.Canceled) || queries.count.Load() != 0 {
		t.Fatal("canceled admission queried")
	}
	db.Close()
	if got, err := s.ClientID(context.Background(), "google"); got != nil || err == nil {
		t.Fatal("unavailable database enabled environment fallback")
	}
}

func TestSocialConfigurationBatchPostgres(t *testing.T) {
	db, queries, s := socialConfigDatabase(t)
	got, err := s.ClientID(context.Background(), "google")
	if err != nil || got != "env-google" || queries.count.Load() != 1 {
		t.Fatal("lowest ID or query bound", got, err, queries.count.Load())
	}
	queries.count.Store(0)
	var group sync.WaitGroup
	failures := make(chan error, 32)
	for i := 0; i < 32; i++ {
		group.Add(1)
		go func() {
			defer group.Done()
			got, err := s.ClientID(context.Background(), "google")
			if err != nil {
				failures <- err
			} else if got != "env-google" {
				failures <- errors.New("concurrent selection changed")
			}
		}()
	}
	group.Wait()
	close(failures)
	for err := range failures {
		t.Error(err)
	}
	if queries.count.Load() != 32 || db.Stats().InUse != 0 || db.Stats().OpenConnections > 4 {
		t.Fatal("concurrent read/pool bound")
	}
	// A real PostgreSQL lock exercises cancellation while a read is pending.
	tx, err := db.Beginx()
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	if _, err = tx.Exec(`LOCK TABLE instance_instanceconfiguration IN ACCESS EXCLUSIVE MODE`); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 75*time.Millisecond)
	defer cancel()
	got, err = s.ClientID(ctx, "google")
	if got != nil || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("blocked read admitted: %v %v", got, err)
	}
	if err = tx.Rollback(); err != nil {
		t.Fatal(err)
	}
	if got, err = s.ClientID(context.Background(), "google"); err != nil || got != "env-google" || db.Stats().InUse != 0 {
		t.Fatal("pool not reusable after cancellation")
	}
}

func TestSocialConfigurationCasesPostgres(t *testing.T) {
	db, queries, s := socialConfigDatabase(t)
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_SOCIAL_CONFIGURATION_FIXTURE"))
	if err != nil {
		t.Fatal("run accounts adapter")
	}
	var fixture struct {
		Cases []struct {
			Name, Provider        string
			Expected              any
			Error                 *string
			Hosted                bool
			Integrations, Display json.RawMessage
		}
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	if d.Decode(&fixture) != nil || len(fixture.Cases) != 33 {
		t.Fatal("invalid configuration fixture")
	}
	var before domain.SocialConfiguration
	if err = db.Get(&before, `SELECT self_hosted,integration_settings,display_settings FROM instance_instanceconfiguration WHERE id=760001`); err != nil {
		t.Fatal(err)
	}
	defer func() {
		if _, err := db.Exec(`UPDATE instance_instanceconfiguration SET self_hosted=$1,integration_settings=$2::jsonb,display_settings=$3::jsonb WHERE id=760001`, before.SelfHosted, []byte(before.Integrations), []byte(before.Display)); err != nil {
			t.Error("restore failed", err)
		}
	}()
	for _, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			if _, err = db.Exec(`UPDATE instance_instanceconfiguration SET self_hosted=$1,integration_settings=$2::jsonb,display_settings=$3::jsonb WHERE id=760001`, tc.Hosted, []byte(tc.Integrations), []byte(tc.Display)); err != nil {
				t.Fatal(err)
			}
			queries.count.Store(0)
			got, err := s.ClientID(context.Background(), tc.Provider)
			if tc.Error != nil {
				if !errors.Is(err, ErrSocialConfiguration) || got != nil {
					t.Fatalf("malformed stored configuration admitted: %v %v", got, err)
				}
			} else if err != nil || !reflect.DeepEqual(got, tc.Expected) {
				t.Fatalf("got %#v %v; want %#v", got, err, tc.Expected)
			}
			if queries.count.Load() != 1 || db.Stats().InUse != 0 {
				t.Fatal("query or connection bound")
			}
		})
	}
}
