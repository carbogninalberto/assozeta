//go:build profilepasswordintegration

package service

import (
	"context"
	"errors"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	passwordcrypto "github.com/carbogninalberto/assozeta/BE_GO/pkg/crypto"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

type profilePasswordQueries struct{ reads, writes atomic.Int64 }

func (q *profilePasswordQueries) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	sql := strings.ToLower(strings.TrimSpace(d.SQL))
	if strings.HasPrefix(sql, "select") {
		q.reads.Add(1)
	}
	if strings.HasPrefix(sql, "update") {
		q.writes.Add(1)
	}
	return ctx
}
func (*profilePasswordQueries) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}
func TestProfilePasswordPostgres(t *testing.T) {
	dsn := os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN")
	if dsn == "" {
		t.Fatal("run accounts.py --only profile-password")
	}
	cfg, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	q := &profilePasswordQueries{}
	cfg.Tracer = q
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	db := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(4)
	db.SetMaxIdleConns(4)
	ctx, cancel := context.WithTimeout(context.Background(), 180*time.Second)
	defer cancel()
	const id = "00000000-0000-4000-8000-000000086000"
	var before string
	if err = db.GetContext(ctx, &before, `SELECT password FROM bakney_user WHERE user_id=$1`, id); err != nil {
		t.Fatal(err)
	}
	restore := func() {
		cleanup, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if _, err := db.ExecContext(cleanup, `UPDATE bakney_user SET password=$2 WHERE user_id=$1`, id, before); err != nil {
			t.Fatal(err)
		}
	}
	defer restore()
	person := &domain.Account{ID: id, Role: 2}
	identity := domain.ResolvedIdentity{Actor: person, Effective: person, ScopeUser: person}
	flow := NewProfilePasswords(repository.NewProfilePasswords(db), passwordcrypto.NewPasswordVerifier(passwordcrypto.DefaultPasswordLimits()), 2)
	input := dto.ProfilePasswordRequest{Current: "Previous1!pass", New: "Replacement2!pass"}
	q.reads.Store(0)
	q.writes.Store(0)
	got, err := flow.Change(ctx, identity, input)
	if err != nil || got != ProfilePasswordChanged || q.reads.Load() != 2 || q.writes.Load() != 1 {
		t.Fatalf("changed=%v err=%v reads=%d writes=%d", got, err, q.reads.Load(), q.writes.Load())
	}
	q.reads.Store(0)
	q.writes.Store(0)
	got, err = flow.Change(ctx, identity, input)
	if err != nil || got != ProfilePasswordIncorrect || q.reads.Load() != 2 || q.writes.Load() != 0 {
		t.Fatalf("old credential accepted: %v %v", got, err)
	}
	restore()
	q.reads.Store(0)
	q.writes.Store(0)
	var wg sync.WaitGroup
	var changed, incorrect atomic.Int64
	for i := 0; i < 24; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			outcome, err := flow.Change(ctx, identity, input)
			if err != nil {
				t.Error(err)
				return
			}
			if outcome == ProfilePasswordChanged {
				changed.Add(1)
			} else if outcome == ProfilePasswordIncorrect {
				incorrect.Add(1)
			}
		}()
	}
	wg.Wait()
	if changed.Load() != 1 || incorrect.Load() != 23 || q.reads.Load() != 48 || q.writes.Load() != 1 {
		t.Fatalf("concurrent outcomes %d/%d queries %d/%d", changed.Load(), incorrect.Load(), q.reads.Load(), q.writes.Load())
	}
	restore()
	blocker, err := db.BeginTxx(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	var locked string
	if err = blocker.GetContext(ctx, &locked, `SELECT password FROM bakney_user WHERE user_id=$1 FOR UPDATE`, id); err != nil {
		blocker.Rollback()
		t.Fatal(err)
	}
	blocked, stop := context.WithTimeout(ctx, 50*time.Millisecond)
	_, err = flow.Change(blocked, identity, input)
	stop()
	blocker.Rollback()
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("locked read did not cancel: %v", err)
	}
	if db.Stats().InUse != 0 {
		t.Fatal("cancelled mutation retained connections")
	}
	if _, err = flow.Change(ctx, identity, input); err != nil {
		t.Fatal("no recovery", err)
	}
	restore()
	_, err = db.ExecContext(ctx, `CREATE FUNCTION profile_password_reject() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic password write failure'; END $$`)
	if err != nil {
		t.Fatal(err)
	}
	defer db.ExecContext(context.Background(), `DROP FUNCTION profile_password_reject()`)
	_, err = db.ExecContext(ctx, `CREATE TRIGGER profile_password_reject BEFORE UPDATE OF password ON bakney_user FOR EACH ROW EXECUTE FUNCTION profile_password_reject()`)
	if err != nil {
		t.Fatal(err)
	}
	_, writeErr := flow.Change(ctx, identity, input)
	_, dropErr := db.ExecContext(ctx, `DROP TRIGGER profile_password_reject ON bakney_user`)
	if dropErr != nil {
		t.Fatal(dropErr)
	}
	if writeErr == nil {
		t.Fatal("write failure accepted")
	}
	var after string
	if err = db.GetContext(ctx, &after, `SELECT password FROM bakney_user WHERE user_id=$1`, id); err != nil || after != before {
		t.Fatal("failed write persisted", err)
	}
	if db.Stats().InUse != 0 || db.Stats().OpenConnections > 4 {
		t.Fatal("pool was not bounded or released")
	}
}
