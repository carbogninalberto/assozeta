//go:build profileintegration

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

	ownedpgx "github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/jackc/pgx/v5"
	"github.com/jmoiron/sqlx"
)

type profileQueries struct{ count atomic.Int64 }

func (q *profileQueries) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	if strings.HasPrefix(strings.ToLower(strings.TrimSpace(d.SQL)), "select") {
		q.count.Add(1)
	}
	return ctx
}
func (*profileQueries) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

func TestLoginProfileAgainstDjango(t *testing.T) {
	path := os.Getenv("ASSOZETA_LOGIN_PROFILE_FIXTURE")
	if path == "" {
		t.Fatal("run TESTS/adapters/django/accounts.py")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Now   time.Time
		Cases []struct {
			Name, ID, Outcome string
			Expected          json.RawMessage
			Queries           int
		}
	}
	if json.Unmarshal(data, &fixture) != nil || len(fixture.Cases) == 0 {
		t.Fatal("invalid profile fixture")
	}
	cfg, err := pgx.ParseConfig(os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN"))
	if err != nil {
		t.Fatal("invalid private database configuration")
	}
	counter := &profileQueries{}
	cfg.Tracer = counter
	db := sqlx.NewDb(ownedpgx.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(4)
	flow := NewLoginProfiles(repository.NewLoginProfiles(db), func() time.Time { return fixture.Now })
	equalJSON := func(actual, expected []byte) bool {
		var a, b any
		da, db := json.NewDecoder(bytes.NewReader(actual)), json.NewDecoder(bytes.NewReader(expected))
		da.UseNumber()
		db.UseNumber()
		return da.Decode(&a) == nil && db.Decode(&b) == nil && reflect.DeepEqual(a, b)
	}
	for _, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			// Count the shared server fence in addition to the unchanged Django query baseline.
			counter.count.Store(0)
			profile, err := flow.Read(context.Background(), tc.ID)
			outcome := ""
			switch {
			case err == nil:
			case errors.Is(err, domain.ErrAccountNotFound):
				outcome = "account_missing"
			case errors.Is(err, domain.ErrAssociationNotFound):
				outcome = "association_missing"
			default:
				t.Fatalf("profile read: %v", err)
			}
			if outcome != tc.Outcome {
				t.Fatalf("outcome %q want %q", outcome, tc.Outcome)
			}
			if outcome != "" {
				if profile != nil {
					t.Fatal("failed profile returned data")
				}
			} else {
				actual, err := json.Marshal(profile)
				if err != nil || !equalJSON(actual, tc.Expected) {
					t.Fatalf("profile JSON mismatch: got %s want %s", actual, tc.Expected)
				}
			}
			if got := counter.count.Load(); got != int64(tc.Queries+1) {
				t.Fatalf("queries %d want %d", got, tc.Queries+1)
			}
		})
	}
	t.Run("onboarding permission failure preserves base profile", func(t *testing.T) {
		var target string
		var expected json.RawMessage
		for _, tc := range fixture.Cases {
			if tc.Name == "onboarding count 1" {
				target = tc.ID
				expected = tc.Expected
			}
		}
		if target == "" {
			t.Fatal("missing singleton onboarding fixture")
		}
		deniedCfg := cfg.Copy()
		deniedCfg.RuntimeParams["role"] = "profile_onboarding_denied"
		deniedDB := sqlx.NewDb(ownedpgx.OpenDB(*deniedCfg), "pgx")
		defer deniedDB.Close()
		deniedDB.SetMaxOpenConns(1)
		deniedFlow := NewLoginProfiles(repository.NewLoginProfiles(deniedDB), func() time.Time { return fixture.Now })
		profile, err := deniedFlow.Read(context.Background(), target)
		if err != nil || profile == nil {
			t.Fatalf("optional onboarding failure discarded base profile: %v", err)
		}
		var want map[string]json.RawMessage
		if json.Unmarshal(expected, &want) != nil {
			t.Fatal("invalid expected profile")
		}
		want["onboarding"] = json.RawMessage("null")
		expected, err = json.Marshal(want)
		if err != nil {
			t.Fatal(err)
		}
		actual, err := json.Marshal(profile)
		if err != nil || !equalJSON(actual, expected) {
			t.Fatal("optional failure altered other profile fields")
		}
		if deniedDB.Stats().InUse != 0 {
			t.Fatal("failed read retained aborted transaction")
		}
		// Pool reuse must work after PostgreSQL aborts the read transaction.
		if _, err = deniedFlow.Read(context.Background(), target); err != nil {
			t.Fatal("aborted transaction poisoned the connection pool")
		}
	})
	t.Run("bounded pool concurrent duplicate onboarding profiles", func(t *testing.T) {
		var target string
		for _, tc := range fixture.Cases {
			if tc.Name == "onboarding count 512" {
				target = tc.ID
			}
		}
		if target == "" {
			t.Fatal("missing duplicate onboarding fixture")
		}
		counter.count.Store(0)
		var wg sync.WaitGroup
		for i := 0; i < 32; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
				defer cancel()
				profile, err := flow.Read(ctx, target)
				if err != nil || profile == nil || profile.Onboarding != nil {
					t.Error("concurrent duplicate onboarding profile failed")
				}
			}()
		}
		wg.Wait()
		if counter.count.Load() != 96 || db.Stats().InUse != 0 {
			t.Fatal("profile query bound or connection release violated")
		}
	})
	t.Run("fresh persisted preferences on subsequent read", func(t *testing.T) {
		var target string
		for _, tc := range fixture.Cases {
			if tc.Name == "legacy complete owner" {
				target = tc.ID
			}
		}
		if target == "" {
			t.Fatal("missing legacy owner fixture")
		}
		if _, err := db.Exec(`UPDATE application_sportassociation SET logo='' WHERE user_id=$1`, target); err != nil {
			t.Fatal(err)
		}
		defer func() {
			if _, err := db.Exec(`UPDATE application_sportassociation SET logo='fixture-logo' WHERE user_id=$1`, target); err != nil {
				t.Error(err)
			}
		}()
		profile, err := flow.Read(context.Background(), target)
		if err != nil || profile == nil || !profile.RequiresWelcome {
			t.Fatal("stale welcome preference")
		}
		if _, err := db.Exec(`UPDATE application_sportassociation SET logo='fixture-logo' WHERE user_id=$1`, target); err != nil {
			t.Fatal(err)
		}
		profile, err = flow.Read(context.Background(), target)
		if err != nil || profile == nil || profile.RequiresWelcome {
			t.Fatal("restored welcome preference not visible")
		}
	})
	t.Run("cancelled and missing account", func(t *testing.T) {
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		if profile, err := flow.Read(ctx, fixture.Cases[0].ID); !errors.Is(err, context.Canceled) || profile != nil {
			t.Fatal("cancellation ignored")
		}
		if profile, err := flow.Read(context.Background(), "00000000-0000-4000-8000-999999999999"); !errors.Is(err, domain.ErrAccountNotFound) || profile != nil {
			t.Fatal("missing account returned profile")
		}
	})
}
