//go:build messagemutationsintegration

package service

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	ownedpgx "github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jmoiron/sqlx"
)

const mmOwner = "29000000-0000-4000-8000-000000000001"
const mmForeignOwner = "29000000-0000-4000-8000-000000000002"
const mmAssociation = "29000000-0000-4000-8000-000000000010"
const mmForeignAssociation = "29000000-0000-4000-8000-000000000011"

// Requires the parent's fully migrated, private historical PostgreSQL fixture.
// No services/schema are created by these tests. Never silently skip a missing DSN.
func messageMutationDB(t *testing.T) *sqlx.DB {
	t.Helper()
	dsn := os.Getenv("ASSOZETA_MESSAGE_MUTATIONS_DSN")
	if dsn == "" {
		t.Fatal("ASSOZETA_MESSAGE_MUTATIONS_DSN required")
	}
	cfg, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal("invalid private DSN")
	}
	host := filepath.Clean(cfg.Host)
	if !filepath.IsAbs(host) || !strings.HasPrefix(filepath.Base(host), "az-history-") || (filepath.Dir(host) != "/tmp" && filepath.Dir(host) != "/private/tmp") || cfg.Database != "assozeta_contract_historical" || cfg.User != "contracts" || len(cfg.Fallbacks) != 0 {
		t.Fatal("parent-owned historical Unix-socket fixture required")
	}
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	cfg.RuntimeParams["timezone"] = "UTC"
	db := sqlx.NewDb(ownedpgx.OpenDB(*cfg), "pgx")
	db.SetMaxOpenConns(8)
	db.SetMaxIdleConns(8)
	t.Cleanup(func() { _ = db.Close() })
	messageMutationClean(t, db)
	t.Cleanup(func() { messageMutationClean(t, db) })
	messageMutationSeed(t, db)
	return db
}
func messageMutationClean(t *testing.T, db *sqlx.DB) {
	t.Helper()
	for _, query := range []string{
		`DELETE FROM communications_messagetransaction WHERE message_id IN (SELECT message_id FROM communications_message WHERE sport_association_id IN ($1,$2))`,
		`DELETE FROM communications_message WHERE sport_association_id IN ($1,$2)`,
		`DELETE FROM application_auditlogindex WHERE sport_association_id IN ($1,$2)`,
		`DELETE FROM auditlog_logentry WHERE actor_id IN ($1,$2)`,
		`DELETE FROM application_sportassociation WHERE sport_association_id IN ($1,$2)`,
	} {
		a, b := mmAssociation, mmForeignAssociation
		if strings.Contains(query, "actor_id") {
			a, b = mmOwner, mmForeignOwner
		}
		if _, err := db.Exec(query, a, b); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := db.Exec(`DELETE FROM bakney_user WHERE user_id IN ($1,$2)`, mmOwner, mmForeignOwner); err != nil {
		t.Fatal(err)
	}
}

func messageMutationSeed(t *testing.T, db *sqlx.DB) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	users := []struct {
		ID       string `json:"id"`
		Username string `json:"username"`
		Email    string `json:"email"`
	}{
		{mmOwner, "message-mutations-owner", "message-mutations-owner@example.test"},
		{mmForeignOwner, "message-mutations-foreign", "message-mutations-foreign@example.test"},
	}
	usersJSON, err := json.Marshal(users)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = db.ExecContext(ctx, `INSERT INTO bakney_user
 (password,is_superuser,username,first_name,last_name,email,is_staff,is_active,date_joined,user_id,role,
  two_fa,stripe_on_boarding_completed,enumerate_invoices,online_payments,balance_sheet_year,
  balance_sheet_start_day,balance_sheet_start_month,temporary_invoice_deletion,auto_archive,auto_mark_attendance,
  full_installments_plan,payment_date_equal_invoice_date,starting_number_invoices,auto_paid_payment,tables_settings,
  deleted,collaborator_role,dark_mode,integration_google_auto_sync,subscription_duration_equal_sport_year,
  subscription_duration,membership_duration,subscription_start_day,subscription_start_month,custom_end_date,
  subscription_end_day,subscription_end_month,membership_starting_number,disable_account_creation,
  force_account_creation,medical_certificate_notifications,hide_category_name,show_zero_payments,imported_from_associami)
 SELECT '!test-fixture',FALSE,r.username,'Fixture','Category',r.email,FALSE,TRUE,now(),r.id,1,
  FALSE,FALSE,FALSE,TRUE,1,1,1,FALSE,FALSE,FALSE,FALSE,FALSE,1,FALSE,'{}'::jsonb,
  FALSE,1,FALSE,FALSE,FALSE,1,1,1,1,FALSE,1,1,1,FALSE,FALSE,FALSE,FALSE,FALSE,FALSE
 FROM jsonb_to_recordset($1::jsonb) AS r(id uuid,username text,email text)`, string(usersJSON)); err != nil {
		t.Fatal(err)
	}
	if _, err = db.ExecContext(ctx, `INSERT INTO application_sportassociation
 (sport_association_id,user_id,denomination,tax_code,additional_sections,subscription_fee,
  multiple_subscription_fee,subscription_fee_plans,multiple_membership_fee,membership_fee_plans,
  enable_quotes_management,configuration,reviewed,deleted,review_url_enabled,imported_from_associami,
  show_regulation_to_members,show_regulation_to_both,show_regulation_to_athletes,
  show_demand_to_members,show_demand_to_both,show_demand_to_athletes)
 VALUES($1,$2,'Message Mutations Owner','29000000001','{}'::jsonb,0,FALSE,'[]'::jsonb,FALSE,'[]'::jsonb,
  TRUE,'{}'::jsonb,FALSE,FALSE,FALSE,FALSE,TRUE,TRUE,TRUE,TRUE,TRUE,TRUE),
 ($3,$4,'Message Mutations Foreign','29000000002','{}'::jsonb,0,FALSE,'[]'::jsonb,FALSE,'[]'::jsonb,
  TRUE,'{}'::jsonb,FALSE,FALSE,FALSE,FALSE,TRUE,TRUE,TRUE,TRUE,TRUE,TRUE)`,
		mmAssociation, mmOwner, mmForeignAssociation, mmForeignOwner); err != nil {
		t.Fatal(err)
	}
}

func mmIdentity() domain.ResolvedIdentity {
	a := &domain.Account{ID: mmOwner, Email: "message-mutations-owner@example.test", Role: domain.RoleAssociation, Active: true}
	return domain.ResolvedIdentity{Actor: a, Effective: a, ScopeUser: a}
}
func mmInsert(t *testing.T, db *sqlx.DB, association, kind string) string {
	t.Helper()
	id := uuid.NewString()
	if _, err := db.Exec(`INSERT INTO communications_message(message_id,sport_association_id,type,message,subject,created_at) VALUES($1,$2,$3,' <b>body</b> ',NULL,NULL)`, id, association, kind); err != nil {
		t.Fatal(err)
	}
	return id
}
func mmDeliver(t *testing.T, db *sqlx.DB, id string) string {
	t.Helper()
	delivery := uuid.NewString()
	if _, err := db.Exec(`INSERT INTO communications_messagetransaction(message_transaction_id,message_id,recipient,sent_on) VALUES($1,$2,'recipient@example.test',now())`, delivery, id); err != nil {
		t.Fatal(err)
	}
	return delivery
}
func mmCount(t *testing.T, db *sqlx.DB, query string, args ...any) int {
	t.Helper()
	var n int
	if err := db.Get(&n, query, args...); err != nil {
		t.Fatal(err)
	}
	return n
}
func mmResult(t *testing.T, ch <-chan error) error {
	t.Helper()
	select {
	case err := <-ch:
		return err
	case <-time.After(8 * time.Second):
		t.Fatal("mutation did not complete/clean up")
		return nil
	}
}

// Injection after the concrete audit/index write tests rollback of real rows,
// rather than replacing the persistence path with mocks.
type mmInterceptStore struct {
	domain.MessageMutationStore
	afterAudit func() error
	afterLock  func()
}
type mmInterceptTx struct {
	domain.MessageMutationTransaction
	store *mmInterceptStore
}

func (s *mmInterceptStore) BeginMessageMutations(ctx context.Context) (domain.MessageMutationTransaction, error) {
	tx, err := s.MessageMutationStore.BeginMessageMutations(ctx)
	if err != nil {
		return nil, err
	}
	return &mmInterceptTx{tx, s}, nil
}
func (tx *mmInterceptTx) AppendMessageAudit(ctx context.Context, a domain.MessageMutationAudit) error {
	if err := tx.MessageMutationTransaction.AppendMessageAudit(ctx, a); err != nil {
		return err
	}
	if tx.store.afterAudit != nil {
		return tx.store.afterAudit()
	}
	return nil
}
func (tx *mmInterceptTx) MessageForUpdate(ctx context.Context, id, association string) (domain.MessageMutationState, error) {
	s, err := tx.MessageMutationTransaction.MessageForUpdate(ctx, id, association)
	if err == nil && tx.store.afterLock != nil {
		tx.store.afterLock()
	}
	return s, err
}

func TestMessageMutationsPostgresCreateDeleteActualCascadeAudit(t *testing.T) {
	db := messageMutationDB(t)
	var relations []struct {
		Table      string `db:"table_name"`
		Deletion   string `db:"delete_action"`
		Deferrable bool   `db:"condeferrable"`
	}
	if err := db.Select(&relations, `SELECT c.relname AS table_name,k.confdeltype::text AS delete_action,k.condeferrable FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid WHERE k.contype='f' AND k.confrelid='communications_message'::regclass`); err != nil {
		t.Fatal(err)
	}
	if len(relations) != 1 || relations[0].Table != "communications_messagetransaction" {
		t.Fatalf("uninspected Message reverse FKs: %#v", relations)
	}
	t.Logf("actual Message reverse FK: %#v", relations[0])
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	at := time.Date(2026, 10, 26, 9, 10, 11, 123456000, time.UTC)
	svc := NewMessageMutations(repository.NewMessageMutations(db), func() time.Time { return at })
	if err := svc.Add(ctx, mmIdentity(), dto.MessageMutationWrite{Type: "EMAIL", Message: "  <b>& body</b> \n"}, domain.LoginAuditContext{}); err != nil {
		t.Fatal(err)
	}
	var row domain.MessageMutationState
	if err := db.Get(&row, `SELECT message_id,sport_association_id,type,message,subject,created_at FROM communications_message WHERE sport_association_id=$1`, mmAssociation); err != nil {
		t.Fatal(err)
	}
	if row.Message != "  <b>& body</b> \n" || row.Subject != nil || row.CreatedAt == nil {
		t.Fatalf("stored %#v", row)
	}
	if n := mmCount(t, db, `SELECT count(*) FROM communications_messagetransaction WHERE message_id=$1`, row.ID); n != 0 {
		t.Fatal("CREATE sent deliveries")
	}
	delivery := mmDeliver(t, db, row.ID)
	_ = delivery
	foreign := mmInsert(t, db, mmForeignAssociation, "EMAIL")
	foreignDelivery := mmDeliver(t, db, foreign)
	if err := svc.Delete(ctx, mmIdentity(), foreign, domain.LoginAuditContext{}); !errors.Is(err, domain.ErrMessageMutationNotFound) {
		t.Fatalf("foreign delete %v", err)
	}
	if err := svc.Delete(ctx, mmIdentity(), row.ID, domain.LoginAuditContext{}); err != nil {
		t.Fatal(err)
	}
	for _, query := range []string{`SELECT count(*) FROM communications_message WHERE message_id=$1`, `SELECT count(*) FROM communications_messagetransaction WHERE message_id=$1`} {
		if n := mmCount(t, db, query, row.ID); n != 0 {
			t.Fatal("cascade incomplete", n)
		}
	}
	if n := mmCount(t, db, `SELECT count(*) FROM communications_messagetransaction WHERE message_transaction_id=$1`, foreignDelivery); n != 1 {
		t.Fatal("foreign delivery changed")
	}
	var audits []struct {
		Action  int    `db:"action"`
		Changes []byte `db:"changes"`
		Path    string `db:"resolution_path"`
		Model   string `db:"model"`
		App     string `db:"app_label"`
		Actor   string `db:"actor_id"`
		Email   string `db:"actor_email"`
		Repr    string `db:"object_repr"`
	}
	if err := db.Select(&audits, `SELECT l.action,l.changes,i.resolution_path,c.model,c.app_label,l.actor_id,l.actor_email,l.object_repr FROM auditlog_logentry l JOIN django_content_type c ON c.id=l.content_type_id JOIN application_auditlogindex i ON i.log_entry_id=l.id WHERE l.object_pk=$1 ORDER BY l.action`, row.ID); err != nil {
		t.Fatal(err)
	}
	if len(audits) != 2 {
		t.Fatalf("audits %d", len(audits))
	}
	for i, a := range audits {
		var changes map[string][2]string
		if err := json.Unmarshal(a.Changes, &changes); err != nil {
			t.Fatal(err)
		}
		expectedAction := i * 2
		path := "direct"
		if i == 1 {
			path = "changes_json.sport_association"
		}
		if a.Action != expectedAction || a.Path != path || a.Model != "message" || a.App != "communications" || a.Actor != mmOwner || a.Email != mmIdentity().Actor.Email || a.Repr != "Message object ("+row.ID+")" {
			t.Fatalf("audit %#v", a)
		}
		if _, present := changes["subject"]; present {
			t.Fatal("null subject audited")
		}
		if _, present := changes["messagetransaction"]; present {
			t.Fatal("reverse relation unexpectedly audited")
		}
		expected := map[string][2]string{"message_id": {"None", row.ID}, "sport_association": {"None", mmAssociation}, "type": {"None", "EMAIL"}, "message": {"None", "  <b>& body</b> \n"}, "created_at": {"None", "2026-10-26 09:10:11.123456"}}
		if i == 1 {
			for key, pair := range expected {
				expected[key] = [2]string{pair[1], pair[0]}
			}
		}
		if !reflect.DeepEqual(changes, expected) {
			t.Fatalf("changes %#v", changes)
		}
	}
	if err := svc.Delete(ctx, mmIdentity(), row.ID, domain.LoginAuditContext{}); !errors.Is(err, domain.ErrMessageMutationNotFound) {
		t.Fatalf("repeat %v", err)
	}
	// DELETE intentionally includes legacy types and nullable timestamps.
	legacy := mmInsert(t, db, mmAssociation, "LEGACY")
	mmDeliver(t, db, legacy)
	if err := svc.Delete(ctx, mmIdentity(), legacy, domain.LoginAuditContext{}); err != nil {
		t.Fatal(err)
	}
}

func TestMessageMutationsPostgresRollbackAndCancellation(t *testing.T) {
	db := messageMutationDB(t)
	mmSeedSentinels(t, db)
	for _, mode := range []string{"after-audit", "bad-audit-port", "cancel"} {
		t.Run(mode, func(t *testing.T) {
			id := mmInsert(t, db, mmAssociation, "EMAIL")
			mmDeliver(t, db, id)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			store := &mmInterceptStore{MessageMutationStore: repository.NewMessageMutations(db)}
			audit := domain.LoginAuditContext{}
			switch mode {
			case "after-audit":
				store.afterAudit = func() error { return errors.New("injected after audit/index") }
			case "cancel":
				store.afterAudit = func() error { cancel(); return nil }
			case "bad-audit-port":
				p := json.Number("2147483648")
				audit.RemotePort = &p
			}
			before := mmSnapshot(t, db)
			err := NewMessageMutations(store, time.Now).Delete(ctx, mmIdentity(), id, audit)
			if err == nil {
				t.Fatal("failure injection accepted")
			}
			mmAssertSnapshot(t, db, before)
			if n := mmCount(t, db, `SELECT count(*) FROM communications_message WHERE message_id=$1`, id); n != 1 {
				t.Fatal("parent not rolled back")
			}
			if n := mmCount(t, db, `SELECT count(*) FROM communications_messagetransaction WHERE message_id=$1`, id); n != 1 {
				t.Fatal("delivery not rolled back")
			}
			if n := mmCount(t, db, `SELECT count(*) FROM auditlog_logentry WHERE object_pk=$1`, id); n != 0 {
				t.Fatal("audit not rolled back")
			}
		})
	}
	for _, mode := range []string{"audit-error", "cancel"} {
		t.Run("create-"+mode, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			store := &mmInterceptStore{MessageMutationStore: repository.NewMessageMutations(db), afterAudit: func() error {
				if mode == "cancel" {
					cancel()
					return nil
				}
				return errors.New("after create audit")
			}}
			before := mmSnapshot(t, db)
			err := NewMessageMutations(store, time.Now).Add(ctx, mmIdentity(), dto.MessageMutationWrite{Type: "EMAIL", Message: "rollback"}, domain.LoginAuditContext{})
			if err == nil {
				t.Fatal("expected failed create")
			}
			mmAssertSnapshot(t, db, before)
		})
	}
}

func TestMessageMutationsPostgresConcurrentDelete(t *testing.T) {
	db := messageMutationDB(t)
	id := mmInsert(t, db, mmAssociation, "EMAIL")
	mmDeliver(t, db, id)
	svc := NewMessageMutations(repository.NewMessageMutations(db), time.Now)
	results := make(chan error, 2)
	for range 2 {
		go func() {
			ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
			defer cancel()
			results <- svc.Delete(ctx, mmIdentity(), id, domain.LoginAuditContext{})
		}()
	}
	success, missing := 0, 0
	for range 2 {
		err := mmResult(t, results)
		if err == nil {
			success++
		} else if errors.Is(err, domain.ErrMessageMutationNotFound) {
			missing++
		} else {
			t.Fatal(err)
		}
	}
	if success != 1 || missing != 1 {
		t.Fatal(success, missing)
	}
	if n := mmCount(t, db, `SELECT count(*) FROM auditlog_logentry WHERE object_pk=$1 AND action=2`, id); n != 1 {
		t.Fatal("duplicate audit", n)
	}
}

func TestMessageMutationsPostgresRestoreBarrierCancellation(t *testing.T) {
	db := messageMutationDB(t)
	barrier, err := db.Beginx()
	if err != nil {
		t.Fatal(err)
	}
	defer barrier.Rollback()
	if _, err = barrier.Exec(`SELECT pg_advisory_xact_lock($1::int,$2::int)`, 1095981639, 1381192786); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 150*time.Millisecond)
	defer cancel()
	result := make(chan error, 1)
	go func() {
		result <- NewMessageMutations(repository.NewMessageMutations(db), time.Now).Add(ctx, mmIdentity(), dto.MessageMutationWrite{Type: "EMAIL", Message: "barrier"}, domain.LoginAuditContext{})
	}()
	if err = mmResult(t, result); err == nil || !errors.Is(ctx.Err(), context.DeadlineExceeded) {
		t.Fatal("exclusive restore barrier admitted mutation")
	}
	if err = barrier.Rollback(); err != nil {
		t.Fatal(err)
	}
	if n := mmCount(t, db, `SELECT count(*) FROM communications_message WHERE sport_association_id=$1`, mmAssociation); n != 0 {
		t.Fatal("cancelled waiter wrote after restore")
	}
	// The shared owned connector must finish cleanup and return a usable pool.
	if err = db.Ping(); err != nil {
		t.Fatal(err)
	}
}

func TestMessageMutationsPostgresConcurrentDeliveryWriter(t *testing.T) {
	for _, immediate := range []bool{false, true} {
		name := "deferred-until-commit"
		if immediate {
			name = "immediate-before-insert"
		}
		t.Run(name, func(t *testing.T) { mmConcurrentDeliveryWriter(t, immediate) })
	}
}
func mmConcurrentDeliveryWriter(t *testing.T, immediate bool) {
	db := messageMutationDB(t)
	var constraint struct {
		Deferrable bool `db:"condeferrable"`
		Deferred   bool `db:"condeferred"`
	}
	if err := db.Get(&constraint, `SELECT condeferrable,condeferred FROM pg_constraint WHERE contype='f' AND conrelid='communications_messagetransaction'::regclass AND confrelid='communications_message'::regclass`); err != nil {
		t.Fatal(err)
	}
	if !constraint.Deferrable || !constraint.Deferred {
		t.Fatalf("expected production DEFERRABLE INITIALLY DEFERRED FK: %#v", constraint)
	}
	mmSeedSentinels(t, db)
	preserved := mmSnapshot(t, db)
	id := mmInsert(t, db, mmAssociation, "EMAIL")
	mmDeliver(t, db, id)
	locked, release := make(chan struct{}), make(chan struct{})
	defer func() {
		select {
		case <-release:
		default:
			close(release)
		}
	}()
	store := &mmInterceptStore{MessageMutationStore: repository.NewMessageMutations(db), afterLock: func() { close(locked); <-release }}
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	deleting := make(chan error, 1)
	go func() {
		deleting <- NewMessageMutations(store, time.Now).Delete(ctx, mmIdentity(), id, domain.LoginAuditContext{})
	}()
	select {
	case <-locked:
	case <-ctx.Done():
		t.Fatal("delete never acquired message lock")
	}
	writer, err := db.BeginTxx(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		// Release the paused delete before joining a blocked writer rollback.
		select {
		case <-release:
		default:
			close(release)
		}
		_ = writer.Rollback()
	}()
	var pid int
	if err = writer.GetContext(ctx, &pid, `SELECT pg_backend_pid()`); err != nil {
		t.Fatal(err)
	}
	if immediate {
		if _, err = writer.ExecContext(ctx, `SET CONSTRAINTS ALL IMMEDIATE`); err != nil {
			t.Fatal(err)
		}
	}
	inserted := make(chan struct{})
	writing := make(chan error, 1)
	go func() {
		_, err := writer.ExecContext(ctx, `INSERT INTO communications_messagetransaction(message_transaction_id,message_id,recipient,sent_on) VALUES($1,$2,'racing@example.test',now())`, uuid.NewString(), id)
		if err == nil {
			close(inserted)
			err = writer.Commit()
		}
		writing <- err
	}()
	if !immediate {
		select {
		case <-inserted: // default FK permits INSERT, then COMMIT must wait
		case err := <-writing:
			t.Fatalf("deferred INSERT failed before COMMIT: %v", err)
		case <-ctx.Done():
			t.Fatal("deferred INSERT did not finish before FK validation")
		}
	}
	// Observe the actual FK lock wait, avoiding a scheduler-dependent sleep race.
	ticker := time.NewTicker(5 * time.Millisecond)
	defer ticker.Stop()
	for {
		var waiting bool
		if err = db.GetContext(ctx, &waiting, `SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=$1 AND wait_event_type='Lock')`, pid); err != nil {
			t.Fatal(err)
		}
		if waiting {
			break
		}
		select {
		case err := <-writing:
			t.Fatalf("FK writer bypassed parent row lock: %v", err)
		case <-ticker.C:
		case <-ctx.Done():
			t.Fatal("writer did not reach FK lock wait")
		}
	}
	if immediate {
		select {
		case <-inserted:
			t.Fatal("immediate INSERT bypassed locked parent")
		default:
		}
	}
	close(release)
	if err = mmResult(t, deleting); err != nil {
		t.Fatal(err)
	}
	err = mmResult(t, writing)
	_ = writer.Rollback()
	var pgerr *pgconn.PgError
	if !errors.As(err, &pgerr) || pgerr.Code != "23503" {
		t.Fatalf("expected racing FK rejection (23503), got %v", err)
	}
	if n := mmCount(t, db, `SELECT count(*) FROM communications_messagetransaction WHERE message_id=$1`, id); n != 0 {
		t.Fatal("orphan delivery", n)
	}
	if n := mmCount(t, db, `SELECT count(*) FROM communications_message WHERE message_id=$1`, id); n != 0 {
		t.Fatal("message survived delete", n)
	}
	if n := mmCount(t, db, `SELECT count(*) FROM auditlog_logentry WHERE object_pk=$1 AND action=2`, id); n != 1 {
		t.Fatal("missing/duplicate delete audit", n)
	}
	if n := mmCount(t, db, `SELECT count(*) FROM application_auditlogindex i JOIN auditlog_logentry l ON l.id=i.log_entry_id WHERE l.object_pk=$1 AND i.sport_association_id=$2 AND i.resolution_path='changes_json.sport_association'`, id, mmAssociation); n != 1 {
		t.Fatal("missing/duplicate delete index", n)
	}
	after := mmSnapshotExceptMessage(t, db, id)
	if !reflect.DeepEqual(after, preserved) {
		t.Fatalf("writer race changed sentinel/business/audit/index rows\nbefore: %#v\nafter: %#v", preserved, after)
	}
}

// Compare complete persisted rows, including existing owned/foreign sentinels.
// PostgreSQL sequence gaps are deliberately outside the business/audit invariant.
type mmDatabaseSnapshot map[string]string

func mmSnapshot(t *testing.T, db *sqlx.DB) mmDatabaseSnapshot {
	t.Helper()
	return mmSnapshotExceptMessage(t, db, "")
}
func mmSnapshotExceptMessage(t *testing.T, db *sqlx.DB, excluded string) mmDatabaseSnapshot {
	t.Helper()
	result := mmDatabaseSnapshot{}
	for _, table := range []string{"communications_message", "communications_messagetransaction", "auditlog_logentry", "application_auditlogindex"} {
		query := `SELECT COALESCE(jsonb_agg(to_jsonb(r) ORDER BY to_jsonb(r)::text),'[]'::jsonb) FROM ` + table + ` r`
		var args []any
		if excluded != "" {
			args = []any{excluded}
			switch table {
			case "communications_message", "communications_messagetransaction":
				query += ` WHERE message_id<>$1::uuid`
			case "auditlog_logentry":
				query += ` WHERE object_pk IS DISTINCT FROM $1`
			case "application_auditlogindex":
				query += ` WHERE NOT EXISTS (SELECT 1 FROM auditlog_logentry l WHERE l.id=r.log_entry_id AND l.object_pk=$1)`
			}
		}
		var raw []byte
		if err := db.Get(&raw, query, args...); err != nil {
			t.Fatal(err)
		}
		result[table] = string(raw)
	}
	return result
}
func mmAssertSnapshot(t *testing.T, db *sqlx.DB, before mmDatabaseSnapshot) {
	t.Helper()
	after := mmSnapshot(t, db)
	for table, rows := range before {
		if after[table] != rows {
			t.Errorf("%s changed across failed mutation\nbefore: %s\nafter: %s", table, rows, after[table])
		}
	}
}
func mmSeedSentinels(t *testing.T, db *sqlx.DB) {
	t.Helper()
	svc := NewMessageMutations(repository.NewMessageMutations(db), time.Now)
	for _, owner := range []string{mmOwner, mmForeignOwner} {
		identity := mmIdentity()
		if owner == mmForeignOwner {
			a := &domain.Account{ID: mmForeignOwner, Email: "message-mutations-foreign@example.test", Role: domain.RoleAssociation, Active: true}
			identity = domain.ResolvedIdentity{Actor: a, Effective: a, ScopeUser: a}
		}
		subject := "preserved subject"
		ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
		err := svc.Add(ctx, identity, dto.MessageMutationWrite{Type: "EMAIL", Message: "preserved sentinel", Subject: &subject}, domain.LoginAuditContext{})
		cancel()
		if err != nil {
			t.Fatal(err)
		}
	}
	var ids []string
	if err := db.Select(&ids, `SELECT message_id FROM communications_message WHERE sport_association_id IN ($1,$2)`, mmAssociation, mmForeignAssociation); err != nil {
		t.Fatal(err)
	}
	for _, id := range ids {
		mmDeliver(t, db, id)
	}
}

// These are actual BEFORE INSERT database errors, distinct from the retained
// afterAudit fault/cancellation tests, which fail after both writes succeeded.
func TestMessageMutationsPostgresActualAuditIndexInsertFailures(t *testing.T) {
	db := messageMutationDB(t)
	mmSeedSentinels(t, db)
	for _, table := range []string{"auditlog_logentry", "application_auditlogindex"} {
		for _, deleting := range []bool{false, true} {
			operation := "create"
			if deleting {
				operation = "delete"
			}
			t.Run(operation+"-"+table, func(t *testing.T) {
				id := mmInsert(t, db, mmAssociation, "EMAIL")
				mmDeliver(t, db, id)
				before := mmSnapshot(t, db)
				if _, err := db.Exec(`CREATE FUNCTION mm_mutations_reject_insert() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='message mutations concrete INSERT fault'; END $$`); err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() {
					if _, err := db.Exec(`DROP FUNCTION mm_mutations_reject_insert()`); err != nil {
						t.Error(err)
					}
				})
				condition := `NEW.actor_id='` + mmOwner + `'::uuid`
				if table == "application_auditlogindex" {
					condition = `NEW.sport_association_id='` + mmAssociation + `'::uuid`
				}
				if _, err := db.Exec(`CREATE TRIGGER mm_mutations_reject_insert BEFORE INSERT ON ` + table + ` FOR EACH ROW WHEN (` + condition + `) EXECUTE FUNCTION mm_mutations_reject_insert()`); err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() {
					if _, err := db.Exec(`DROP TRIGGER mm_mutations_reject_insert ON ` + table); err != nil {
						t.Error(err)
					}
				})
				ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
				defer cancel()
				svc := NewMessageMutations(repository.NewMessageMutations(db), time.Now)
				var err error
				if deleting {
					err = svc.Delete(ctx, mmIdentity(), id, domain.LoginAuditContext{})
				} else {
					err = svc.Add(ctx, mmIdentity(), dto.MessageMutationWrite{Type: "EMAIL", Message: "must rollback"}, domain.LoginAuditContext{})
				}
				var pgerr *pgconn.PgError
				if !errors.As(err, &pgerr) || pgerr.Code != "23514" || pgerr.Message != "message mutations concrete INSERT fault" {
					t.Fatalf("expected actual %s INSERT error, got %v", table, err)
				}
				mmAssertSnapshot(t, db, before)
			})
		}
	}
}
