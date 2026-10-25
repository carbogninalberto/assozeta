//go:build profileupdateintegration

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

	ownedpgx "github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/jackc/pgx/v5"
	"github.com/jmoiron/sqlx"
)

type profileMutationQueries struct{ reads, writes, inserts atomic.Int64 }

func (q *profileMutationQueries) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	query := strings.ToLower(strings.TrimSpace(d.SQL))
	switch {
	case strings.HasPrefix(query, "select"):
		q.reads.Add(1)
	case strings.HasPrefix(query, "update"):
		q.writes.Add(1)
	case strings.HasPrefix(query, "insert"):
		q.inserts.Add(1)
	}
	return ctx
}
func (*profileMutationQueries) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}
func (q *profileMutationQueries) reset()                                                        { q.reads.Store(0); q.writes.Store(0); q.inserts.Store(0) }
func (q *profileMutationQueries) expect(t *testing.T, reads, writes, inserts int64) {
	t.Helper()
	if q.reads.Load() != reads || q.writes.Load() != writes || q.inserts.Load() != inserts {
		t.Fatalf("queries select/update/insert=%d/%d/%d; want %d/%d/%d", q.reads.Load(), q.writes.Load(), q.inserts.Load(), reads, writes, inserts)
	}
}

func TestProfileUpdatePostgres(t *testing.T) {
	dsn := os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN")
	if dsn == "" {
		t.Fatal("run accounts.py --only profile-update")
	}
	cfg, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	q := &profileMutationQueries{}
	cfg.Tracer = q
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	db := sqlx.NewDb(ownedpgx.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(4)
	db.SetMaxIdleConns(4)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	const owner = "00000000-0000-4000-8000-000000099004"
	const association = "00000000-0000-4000-8000-000000100004"
	var beforeName, beforeDenomination, email string
	var lastAudit int64
	if err = db.GetContext(ctx, &beforeName, `SELECT first_name FROM bakney_user WHERE user_id=$1`, owner); err != nil {
		t.Fatal(err)
	}
	if err = db.GetContext(ctx, &email, `SELECT email FROM bakney_user WHERE user_id=$1`, owner); err != nil {
		t.Fatal(err)
	}
	if err = db.GetContext(ctx, &beforeDenomination, `SELECT denomination FROM application_sportassociation WHERE sport_association_id=$1`, association); err != nil {
		t.Fatal(err)
	}
	if err = db.GetContext(ctx, &lastAudit, `SELECT COALESCE(MAX(id),0) FROM auditlog_logentry`); err != nil {
		t.Fatal(err)
	}
	restore := func() {
		cleanup, stop := context.WithTimeout(context.Background(), 10*time.Second)
		defer stop()
		if _, err := db.ExecContext(cleanup, `DELETE FROM application_auditlogindex WHERE log_entry_id IN (SELECT id FROM auditlog_logentry WHERE id>$1 AND object_pk IN ($2,$3))`, lastAudit, owner, association); err != nil {
			t.Fatal(err)
		}
		if _, err := db.ExecContext(cleanup, `DELETE FROM auditlog_logentry WHERE id>$1 AND object_pk IN ($2,$3)`, lastAudit, owner, association); err != nil {
			t.Fatal(err)
		}
		if _, err := db.ExecContext(cleanup, `UPDATE bakney_user SET first_name=$2 WHERE user_id=$1`, owner, beforeName); err != nil {
			t.Fatal(err)
		}
		if _, err := db.ExecContext(cleanup, `UPDATE application_sportassociation SET denomination=$2 WHERE sport_association_id=$1`, association, beforeDenomination); err != nil {
			t.Fatal(err)
		}
	}
	defer restore()
	person := &domain.Account{ID: owner, Role: 1, Email: email}
	identity := domain.ResolvedIdentity{Actor: person, Effective: person, ScopeUser: person}
	fields := domain.RequestObject{}
	for _, key := range []string{"denomination", "tax_code", "address", "address_cap", "address_city", "enable_quotes_management", "configuration", "federation", "enroll_number", "sport", "president_first_name", "president_last_name", "stripe_available_methods", "invoice_template", "subscription_template", "extra_text_invoices", "checkout_info"} {
		var value any
		if key == "denomination" {
			value = "SQL ASD"
		}
		fields = append(fields, domain.RequestField{Name: key, Value: value})
	}
	input := dto.ProfileUpdateRequest{Person: map[string]any{"first_name": "SQL Profile"}, Association: fields}
	flow := NewProfileMutations(repository.NewProfileUpdates(db), nil, time.Now)
	q.reset()
	if _, err = flow.Change(ctx, identity, input, domain.LoginAuditContext{}); err != nil {
		t.Fatal(err)
	}
	q.expect(t, 4, 2, 4)
	q.reset()
	if _, err = flow.Change(ctx, identity, input, domain.LoginAuditContext{}); err != nil {
		t.Fatal(err)
	}
	q.expect(t, 3, 2, 0)
	restore()
	q.reset()
	var wg sync.WaitGroup
	for i := 0; i < 24; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, err := flow.Change(ctx, identity, input, domain.LoginAuditContext{}); err != nil {
				t.Error(err)
			}
		}()
	}
	wg.Wait()
	q.expect(t, 73, 48, 4)
	var events int
	if err = db.GetContext(ctx, &events, `SELECT count(*) FROM auditlog_logentry WHERE id>$1`, lastAudit); err != nil || events != 2 {
		t.Fatal("duplicate audit", events, err)
	}
	for _, locked := range []string{"person", "association"} {
		restore()
		blocker, err := db.BeginTxx(ctx, nil)
		if err != nil {
			t.Fatal(err)
		}
		query := `SELECT user_id FROM bakney_user WHERE user_id=$1 FOR UPDATE`
		key := owner
		if locked == "association" {
			query = `SELECT sport_association_id FROM application_sportassociation WHERE sport_association_id=$1 FOR NO KEY UPDATE`
			key = association
		}
		var ignored string
		if err = blocker.GetContext(ctx, &ignored, query, key); err != nil {
			blocker.Rollback()
			t.Fatal(err)
		}
		q.reset()
		blocked, stop := context.WithTimeout(ctx, 100*time.Millisecond)
		_, err = flow.Change(blocked, identity, input, domain.LoginAuditContext{})
		stop()
		blocker.Rollback()
		if !errors.Is(err, context.DeadlineExceeded) {
			t.Fatal(locked, "did not cancel", err)
		}
		if locked == "association" {
			q.expect(t, 4, 1, 2)
		} else {
			q.expect(t, 2, 0, 0)
		}
		if db.Stats().InUse != 0 {
			t.Fatal("cancelled mutation retained connection")
		}
		var after string
		if err = db.GetContext(ctx, &after, `SELECT first_name FROM bakney_user WHERE user_id=$1`, owner); err != nil || after != beforeName {
			t.Fatal("cancelled mutation persisted person", err)
		}
		if err = db.GetContext(ctx, &events, `SELECT count(*) FROM auditlog_logentry WHERE id>$1`, lastAudit); err != nil || events != 0 {
			t.Fatal("cancelled mutation left audit", events, err)
		}
		if _, err = flow.Change(ctx, identity, input, domain.LoginAuditContext{}); err != nil {
			t.Fatal("recovery failed", err)
		}
	}
	restore()
	tx, err := repository.NewProfileUpdates(db).BeginProfileUpdate(ctx)
	if err != nil {
		t.Fatal(err)
	}
	foreign := "00000000-0000-4000-8000-000000000090"
	if _, err = tx.ProfileAssociationForUpdate(ctx, owner, &foreign); !errors.Is(err, domain.ErrAssociationNotFound) {
		tx.Rollback()
		t.Fatal("foreign scope admitted", err)
	}
	if err = tx.SetProfileAssociation(ctx, association, foreign, map[string]any{"denomination": "foreign"}); !errors.Is(err, domain.ErrAssociationNotFound) {
		tx.Rollback()
		t.Fatal("foreign owner write admitted", err)
	}
	if err = tx.SetProfilePerson(ctx, owner, map[string]any{"password": "forged"}); !errors.Is(err, domain.ErrInvalidScopedRequest) {
		tx.Rollback()
		t.Fatal("unlisted column admitted", err)
	}
	tx.Rollback()
	if db.Stats().InUse != 0 || db.Stats().OpenConnections > 4 {
		t.Fatal("pool not bounded or released")
	}
	t.Log("owner mutation: 3 SELECT/2 UPDATE/4 INSERT; unchanged repetition: 2/2/0; 24 competitors: 49/48/4 with two audit records; pre-write and mid-write cancellation, recovery and scope predicates pass")
}
