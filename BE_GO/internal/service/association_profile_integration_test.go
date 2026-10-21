//go:build associationprofileintegration

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
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

type associationQueries struct{ count atomic.Int64 }

func (q *associationQueries) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	if strings.HasPrefix(strings.ToLower(strings.TrimSpace(d.SQL)), "select") {
		q.count.Add(1)
	}
	return ctx
}
func (*associationQueries) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

func TestAssociationProfileAgainstDjango(t *testing.T) {
	path := os.Getenv("ASSOZETA_ASSOCIATION_PROFILE_FIXTURE")
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
			Name, Owner, Association, Outcome string
			Expected                          json.RawMessage
			Queries                           int
		}
	}
	if json.Unmarshal(data, &fixture) != nil || len(fixture.Cases) == 0 {
		t.Fatal("invalid association profile fixture")
	}
	cfg, err := pgx.ParseConfig(os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN"))
	if err != nil {
		t.Fatal("invalid private database configuration")
	}
	queries := &associationQueries{}
	cfg.Tracer = queries
	db := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(4)
	profiles := NewAssociationProfiles(repository.NewAssociationProfiles(db), func() time.Time { return fixture.Now })
	for _, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			queries.count.Store(0)
			result, err := profiles.Read(context.Background(), tc.Owner)
			if tc.Outcome == "missing" {
				if result != nil || !errors.Is(err, domain.ErrAssociationNotFound) {
					t.Fatal("missing/deleted association returned data")
				}
			} else {
				if err != nil || result == nil {
					t.Fatalf("profile unavailable: %v", err)
				}
				actual, err := json.Marshal(result)
				if err != nil {
					t.Fatal(err)
				}
				var a, b any
				da, db := json.NewDecoder(bytes.NewReader(actual)), json.NewDecoder(bytes.NewReader(tc.Expected))
				da.UseNumber()
				db.UseNumber()
				if da.Decode(&a) != nil || db.Decode(&b) != nil || !reflect.DeepEqual(a, b) {
					t.Fatalf("association profile mismatch: got %s want %s", actual, tc.Expected)
				}
			}
			if queries.count.Load() != int64(tc.Queries) {
				t.Fatalf("query count %d want %d", queries.count.Load(), tc.Queries)
			}
		})
	}
	t.Run("bounded pool and all groups", func(t *testing.T) {
		var target string
		for _, tc := range fixture.Cases {
			if tc.Name == "groups 512" {
				target = tc.Owner
			}
		}
		if target == "" {
			t.Fatal("missing group fixture")
		}
		queries.count.Store(0)
		var wg sync.WaitGroup
		for i := 0; i < 32; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
				defer cancel()
				result, err := profiles.Read(ctx, target)
				if err != nil || result == nil || len(result.Groups) != 512 {
					t.Error("concurrent profile truncated groups or failed")
				}
			}()
		}
		wg.Wait()
		if queries.count.Load() != 64 || db.Stats().InUse != 0 {
			t.Fatal("query bound or connection release violated")
		}
	})
	t.Run("association deletion immediately revokes serialization", func(t *testing.T) {
		target := fixture.Cases[0]
		if _, err := db.Exec(`UPDATE application_sportassociation SET deleted=true WHERE sport_association_id=$1`, target.Association); err != nil {
			t.Fatal(err)
		}
		defer func() {
			if _, err := db.Exec(`UPDATE application_sportassociation SET deleted=false WHERE sport_association_id=$1`, target.Association); err != nil {
				t.Error(err)
			}
		}()
		result, err := profiles.Read(context.Background(), target.Owner)
		if result != nil || !errors.Is(err, domain.ErrAssociationNotFound) {
			t.Fatal("deleted association still serialized")
		}
		if _, err := db.Exec(`UPDATE application_sportassociation SET deleted=false WHERE sport_association_id=$1`, target.Association); err != nil {
			t.Fatal(err)
		}
		result, err = profiles.Read(context.Background(), target.Owner)
		if err != nil || result == nil || result.ID != target.Association {
			t.Fatal("restored association was not visible")
		}
	})
	t.Run("required group failure returns no partial association", func(t *testing.T) {
		deniedCfg := cfg.Copy()
		deniedCfg.RuntimeParams["role"] = "association_groups_denied"
		deniedDB := sqlx.NewDb(stdlib.OpenDB(*deniedCfg), "pgx")
		defer deniedDB.Close()
		deniedDB.SetMaxOpenConns(1)
		flow := NewAssociationProfiles(repository.NewAssociationProfiles(deniedDB), func() time.Time { return fixture.Now })
		for i := 0; i < 2; i++ {
			queries.count.Store(0)
			result, err := flow.Read(context.Background(), fixture.Cases[0].Owner)
			var sqlError *pgconn.PgError
			if result != nil || !errors.As(err, &sqlError) || sqlError.Code != "42501" || queries.count.Load() != 2 {
				t.Fatalf("required group permission failure was not exercised: %v", err)
			}
			if deniedDB.Stats().InUse != 0 {
				t.Fatal("failed group read retained aborted transaction")
			}
		}
	})
	t.Run("cancelled read", func(t *testing.T) {
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		result, err := profiles.Read(ctx, fixture.Cases[0].Owner)
		if result != nil || !errors.Is(err, context.Canceled) {
			t.Fatal("cancellation ignored")
		}
	})
}
