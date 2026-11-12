//go:build integration

package repository

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"reflect"
	"sort"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type countedScopeDB struct {
	*sqlx.DB
	queries atomic.Int64
}

func scopeFixtureID(n int) string { return fmt.Sprintf("00000000-0000-4000-8000-%012d", n) }

func (db *countedScopeDB) QueryxContext(ctx context.Context, query string, args ...any) (*sqlx.Rows, error) {
	db.queries.Add(1)
	return db.DB.QueryxContext(ctx, query, args...)
}

func TestScopedResourcesAgainstDjango(t *testing.T) {
	data, err := os.ReadFile(os.Getenv("ASSOZETA_SCOPED_RESOURCES_FIXTURE"))
	if err != nil {
		t.Fatal("run through TESTS/adapters/django/accounts.py: scoped fixture required")
	}
	var cases []struct {
		Name  string
		Scope domain.ResourceScope
		Kind  string
		IDs   []string
		Want  []string
	}
	if err := json.Unmarshal(data, &cases); err != nil {
		t.Fatal(err)
	}
	if len(cases) < 50 {
		t.Fatal("incomplete scope fixtures")
	}
	db := &countedScopeDB{DB: accountDatabase(t)}
	db.SetMaxOpenConns(4)
	repo := NewScopedResources(db)
	for _, tc := range cases {
		t.Run(tc.Name, func(t *testing.T) {
			before := db.queries.Load()
			got, err := repo.Available(context.Background(), tc.Scope, tc.Kind, tc.IDs)
			if err != nil {
				t.Fatal(err)
			}
			sort.Strings(got)
			sort.Strings(tc.Want)
			if len(got) != len(tc.Want) || (len(got) > 0 && !reflect.DeepEqual(got, tc.Want)) {
				t.Fatalf("available = %v; want %v", got, tc.Want)
			}
			if db.queries.Load()-before != 1 {
				t.Fatal("scope batch must execute exactly one query")
			}
		})
	}
	// Increasing reference counts must not produce a query per reference. Every
	// input is a distinct existing row; this is not a duplicate-ID shortcut.
	scope := cases[0].Scope
	for _, count := range []int{1, 16, 128, 512} {
		ids := make([]string, count)
		for i := range ids {
			ids[i] = scopeFixtureID(10000 + i)
		}
		before := db.queries.Load()
		got, err := repo.Available(context.Background(), scope, "tag", ids)
		if err != nil || len(got) != count || db.queries.Load()-before != 1 {
			t.Fatalf("batch %d: rows=%d error=%v queries=%d", count, len(got), err, db.queries.Load()-before)
		}
	}
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			got, err := repo.Available(context.Background(), scope, "tag", []string{scopeFixtureID(10000)})
			if err != nil || len(got) != 1 {
				t.Errorf("concurrent scoped lookup: %v", err)
			}
		}()
	}
	wg.Wait()
	if db.Stats().InUse != 0 {
		t.Fatal("scope lookup leaked a connection")
	}
	// The same repository can read inside the transaction owning a mutation.
	// Loss of the family seed/payment must be observed immediately, with no
	// cached authorization and no persisted changes left for Django readback.
	t.Run("transaction sees revoked family and invoice visibility", func(t *testing.T) {
		tx, err := db.BeginTxx(context.Background(), nil)
		if err != nil {
			t.Fatal(err)
		}
		defer tx.Rollback()
		scoped := NewScopedResources(tx)
		athlete := domain.ResourceScope{AssociationID: scopeFixtureID(80), EffectiveID: scopeFixtureID(30), Role: domain.RoleAthlete, GroupID: scopeFixtureID(1001)}
		check := func(kind string, number, want int) {
			t.Helper()
			got, err := scoped.Available(context.Background(), athlete, kind, []string{scopeFixtureID(number)})
			if err != nil || len(got) != want {
				t.Fatalf("%s visibility=%v error=%v; want %d", kind, got, err, want)
			}
		}
		check("subscription", 2012, 1)
		check("invoice", 4011, 1)
		if _, err := tx.ExecContext(context.Background(), `UPDATE application_payment SET deleted=TRUE WHERE payment_id=$1`, scopeFixtureID(3011)); err != nil {
			t.Fatal(err)
		}
		check("invoice", 4011, 0)
		if _, err := tx.ExecContext(context.Background(), `UPDATE application_subscription SET user_id=$1 WHERE subscription_id=$2`, scopeFixtureID(31), scopeFixtureID(2011)); err != nil {
			t.Fatal(err)
		}
		check("subscription", 2012, 0)
		check("associate", 1011, 0)
	})
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if got, err := repo.Available(ctx, scope, "tag", []string{scopeFixtureID(10000)}); !errors.Is(err, context.Canceled) || len(got) != 0 {
		t.Fatal("cancellation returned authorized rows")
	}
	before := db.queries.Load()
	for _, invalid := range []struct {
		scope domain.ResourceScope
		kind  string
		ids   []string
	}{
		{domain.ResourceScope{}, "tag", []string{scopeFixtureID(10000)}},
		{scope, "tag; DROP TABLE application_tags", []string{scopeFixtureID(10000)}},
		{scope, "tag", []string{"' OR 1=1 --"}},
		{scope, "tag", make([]string, 513)},
	} {
		if got, err := repo.Available(context.Background(), invalid.scope, invalid.kind, invalid.ids); err == nil || len(got) != 0 {
			t.Fatal("invalid scope input admitted")
		}
	}
	if db.queries.Load() != before {
		t.Fatal("invalid references reached PostgreSQL")
	}
	if got, err := repo.Available(context.Background(), scope, "tag", nil); err != nil || len(got) != 0 || db.queries.Load() != before {
		t.Fatal("empty batch must not query")
	}
	db.Close()
	if got, err := repo.Available(context.Background(), scope, "tag", []string{scopeFixtureID(10000)}); err == nil || len(got) != 0 {
		t.Fatal("database failure returned authorized rows")
	}
}
