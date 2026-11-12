//go:build manualtoolserviceintegration

package service_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/manual"
	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

type toolSQLCounter struct {
	*sqlx.DB
	count atomic.Int64
}

func (q *toolSQLCounter) QueryxContext(ctx context.Context, query string, args ...any) (*sqlx.Rows, error) {
	q.count.Add(1)
	return q.DB.QueryxContext(ctx, query, args...)
}

type toolServiceCase struct {
	Settings                                                 manual.Settings
	Name, UserID, AssociationID, OwnerID, OtherAssociationID string
	Arguments, Expected                                      map[string]any
}

func loadToolServiceCase(t *testing.T) (string, toolServiceCase, *sqlx.DB, *toolSQLCounter, *service.ManualTools) {
	t.Helper()
	path := os.Getenv("ASSOZETA_MANUAL_TOOL_SERVICE_FIXTURE")
	if !strings.HasPrefix(filepath.Base(filepath.Dir(path)), "az-manual-http-") {
		t.Fatal("private manual adapter fixture required")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixture toolServiceCase
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	if err = decoder.Decode(&fixture); err != nil {
		t.Fatal(err)
	}
	dsn := os.Getenv("ASSOZETA_MANUAL_TOOL_SERVICE_DSN")
	if !strings.Contains(dsn, "dbname=assozeta_contract_manual_http") || !strings.Contains(dsn, filepath.Dir(path)) {
		t.Fatal("owned database required")
	}
	db, err := sqlx.Connect("pgx", dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { db.Close() })
	db.SetMaxOpenConns(4)
	q := &toolSQLCounter{DB: db}
	return path, fixture, db, q, service.NewManualTools(repository.NewManualToolContext(q), manual.New(fixture.Settings))
}

func toolServiceIdentity(user string) domain.ResolvedIdentity {
	if user == "" {
		return domain.ResolvedIdentity{}
	}
	account := &domain.Account{ID: user, Active: true}
	return domain.ResolvedIdentity{Actor: account, Effective: account, ScopeUser: account}
}

func TestManualToolServiceOracle(t *testing.T) {
	path, fixture, _, q, s := loadToolServiceCase(t)
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	actual, err := s.Execute(ctx, toolServiceIdentity(fixture.UserID), fixture.AssociationID, fixture.Name, fixture.Arguments)
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := json.Marshal(actual)
	if err != nil {
		t.Fatal(err)
	}
	want, _ := json.Marshal(fixture.Expected)
	var a, b any
	json.Unmarshal(encoded, &a)
	json.Unmarshal(want, &b)
	if !reflect.DeepEqual(a, b) {
		t.Fatalf("%s differs\nactual: %s\nexpected: %s", fixture.Name, encoded, want)
	}
	expectedQueries := int64(1)
	if fixture.Name == "search_manual" && fixture.Expected["status"] == "invalid_query" {
		expectedQueries = 0
	}
	if q.count.Load() != expectedQueries {
		t.Fatal("unexpected query count", q.count.Load(), expectedQueries)
	}
	result, _ := json.Marshal(map[string]any{"result": actual, "selects": q.count.Load()})
	if err = os.WriteFile(path+".result.json", result, 0600); err != nil {
		t.Fatal(err)
	}
}

func TestManualToolServiceLiveAuthority(t *testing.T) {
	path, fixture, db, q, s := loadToolServiceCase(t)
	ctx := context.Background()
	identity := toolServiceIdentity(fixture.OwnerID)
	args := map[string]any{"section_id": "guide#private", "user_id": fixture.OwnerID, "maintainer": true}
	call := func(want string) {
		t.Helper()
		before := q.count.Load()
		result, err := s.Execute(ctx, identity, fixture.AssociationID, "get_manual_evidence", args)
		if err != nil || result["status"] != want || q.count.Load() != before+1 {
			t.Fatal(result, err, q.count.Load()-before)
		}
	}
	exec := func(query string, args ...any) {
		t.Helper()
		if _, err := db.ExecContext(ctx, query, args...); err != nil {
			t.Fatal(err)
		}
	}
	call("verified")
	// The long-lived identity still says Active=true; database changes must win.
	for _, column := range []string{"is_active", "deleted"} {
		initial, changed := true, false
		if column == "deleted" {
			initial, changed = false, true
		}
		func() {
			defer exec("UPDATE bakney_user SET "+column+"=$1 WHERE user_id=$2", initial, fixture.OwnerID)
			exec("UPDATE bakney_user SET "+column+"=$1 WHERE user_id=$2", changed, fixture.OwnerID)
			call("forbidden")
		}()
		call("verified")
	}
	func() {
		defer exec("UPDATE instance_instanceconfiguration SET self_hosted=TRUE")
		exec("UPDATE instance_instanceconfiguration SET self_hosted=FALSE")
		call("forbidden")
	}()
	call("verified")
	func() {
		defer exec("UPDATE instance_instanceconfiguration SET primary_association_id=$1", fixture.AssociationID)
		exec("UPDATE instance_instanceconfiguration SET primary_association_id=$1", fixture.OtherAssociationID)
		call("forbidden")
	}()
	call("verified")
	var wg sync.WaitGroup
	before := q.count.Load()
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			id := identity
			want := "verified"
			if i%2 != 0 {
				id = domain.ResolvedIdentity{}
				want = "forbidden"
			}
			result, err := s.Execute(ctx, id, fixture.AssociationID, "get_manual_evidence", args)
			if err != nil || result["status"] != want {
				t.Error(result, err)
			}
		}(i)
	}
	wg.Wait()
	if q.count.Load()-before != 32 {
		t.Fatal("concurrent query budget", q.count.Load()-before)
	}
	// Actual PostgreSQL lock wait must cancel and release the pooled connection.
	func() {
		tx, err := db.BeginTxx(ctx, nil)
		if err != nil {
			t.Fatal(err)
		}
		defer tx.Rollback()
		if _, err = tx.ExecContext(ctx, "LOCK TABLE instance_instanceconfiguration IN ACCESS EXCLUSIVE MODE"); err != nil {
			t.Fatal(err)
		}
		blocked, cancel := context.WithTimeout(ctx, 100*time.Millisecond)
		defer cancel()
		result, err := s.Execute(blocked, identity, fixture.AssociationID, "get_manual_evidence", args)
		if result != nil || !errors.Is(err, context.DeadlineExceeded) {
			t.Fatal("blocked SQL did not cancel", result, err)
		}
	}()
	call("verified")
	result, _ := json.Marshal(map[string]any{"live_account_status": true, "live_instance_and_association": true, "concurrent_calls": 32, "sql_per_call": 1, "blocked_query_cancellation_and_recovery": true})
	if err := os.WriteFile(path+".live.json", result, 0600); err != nil {
		t.Fatal(err)
	}
}
