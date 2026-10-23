//go:build paymentcategoryintegration

package service

import (
	"context"
	"errors"
	"fmt"
	"os"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

// This tracer observes the actual pgx commands, rather than repository method
// calls. The PID identifies the specific PostgreSQL waiter in the cancel test.
type paymentCategoryQueryTrace struct {
	mu           sync.Mutex
	queries      []string
	categoryLock chan uint32
}

func (p *paymentCategoryQueryTrace) TraceQueryStart(ctx context.Context, conn *pgx.Conn, data pgx.TraceQueryStartData) context.Context {
	p.mu.Lock()
	p.queries = append(p.queries, data.SQL)
	p.mu.Unlock()
	if p.categoryLock != nil && strings.Contains(data.SQL, "FOR UPDATE OF c") {
		select {
		case p.categoryLock <- conn.PgConn().PID():
		default:
		}
	}
	return ctx
}

func (*paymentCategoryQueryTrace) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

func (p *paymentCategoryQueryTrace) take() []string {
	p.mu.Lock()
	defer p.mu.Unlock()
	queries := append([]string(nil), p.queries...)
	p.queries = nil
	return queries
}

func paymentCategoryTracedDB(t *testing.T, trace *paymentCategoryQueryTrace) *sqlx.DB {
	t.Helper()
	config, err := pgx.ParseConfig(os.Getenv("ASSOZETA_PAYMENT_CATEGORY_DSN"))
	if err != nil {
		t.Fatal(err)
	}
	config.DefaultQueryExecMode = pgx.QueryExecModeExec
	config.Tracer = trace
	db := sqlx.NewDb(stdlib.OpenDB(*config), "pgx")
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)
	t.Cleanup(func() { _ = db.Close() })
	return db
}

func paymentCategoryOwnedRowsCleanup(t *testing.T, db *sqlx.DB, categories, vats []string) {
	t.Helper()
	// Exact IDs are intentionally outside the existing fixture's prefix cleanup.
	t.Cleanup(func() {
		for _, statement := range []string{
			`DELETE FROM application_auditlogindex WHERE log_entry_id IN (SELECT id FROM auditlog_logentry WHERE object_pk=ANY($1))`,
			`DELETE FROM auditlog_logentry WHERE object_pk=ANY($1)`,
		} {
			if _, err := db.Exec(statement, append(append([]string{}, categories...), vats...)); err != nil {
				t.Error(err)
			}
		}
		if _, err := db.Exec(`DELETE FROM application_paymentcategory WHERE payment_category_id=ANY($1::uuid[])`, categories); err != nil {
			t.Error(err)
		}
		if _, err := db.Exec(`DELETE FROM application_vatmanagement WHERE vat_management_id=ANY($1::uuid[])`, vats); err != nil {
			t.Error(err)
		}
	})
}

func TestPaymentCategoryListQueriesBoundedLive(t *testing.T) {
	db := openPaymentCategoryIntegrationDB(t)
	categoryIDs, vatIDs := make([]string, 128), make([]string, 128)
	for i := range categoryIDs {
		categoryIDs[i] = fmt.Sprintf("19300000-0000-4000-8000-%012d", i+1)
		vatIDs[i] = fmt.Sprintf("19400000-0000-4000-8000-%012d", i+1)
	}
	paymentCategoryOwnedRowsCleanup(t, db, categoryIDs, vatIDs)
	trace := &paymentCategoryQueryTrace{}
	tracedDB := paymentCategoryTracedDB(t, trace)
	flow := NewPaymentCategories(repository.NewPaymentCategories(tracedDB), time.Now)
	association := paymentCategoryDBAssociation
	created := time.Date(2026, 10, 23, 12, 0, 0, 120000000, time.UTC)
	inserted, baseline := 0, 0
	for _, size := range []int{1, 8, 32, 128} {
		for i := inserted; i < size; i++ {
			if _, err := db.Exec(`INSERT INTO application_vatmanagement(vat_management_id,vat,deduction) VALUES($1,$2,$3)`, vatIDs[i], i%17, i%3); err != nil {
				t.Fatal(err)
			}
			insertPaymentCategoryIntegration(t, db, categoryIDs[i], fmt.Sprintf("joined-%d", i), &association, created.Add(time.Duration(i)*time.Second), i%2 == 1, &vatIDs[i])
		}
		inserted = size
		trace.take()
		rows, err := flow.List(context.Background(), paymentCategoryDBIdentity())
		queries := trace.take()
		if err != nil {
			t.Fatal(err)
		}
		if size == 1 {
			baseline = len(queries)
		}
		if len(queries) == 0 || len(queries) > 2 || len(queries) != baseline {
			t.Fatalf("%d rows: list query count %d, baseline %d: %v", size, len(queries), baseline, queries)
		}
		if len(rows) != size {
			t.Fatalf("%d rows: got %d", size, len(rows))
		}
		for position, row := range rows {
			i := size - position - 1
			want := dto.PaymentCategory{
				ID: categoryIDs[i], Name: fmt.Sprintf("joined-%d", i),
				CreationDate:  created.Add(time.Duration(i) * time.Second).Format("2006-01-02T15:04:05.000000Z"),
				AssociationID: &association, Type: 1, Deleted: i%2 == 1,
				VAT: &dto.PaymentCategoryVAT{ID: vatIDs[i], VAT: uint16(i % 17), Deduction: uint16(i % 3)},
			}
			if !reflect.DeepEqual(row, want) {
				t.Fatalf("size %d position %d: got %+v VAT=%+v, want %+v VAT=%+v", size, position, row, row.VAT, want, want.VAT)
			}
		}
		t.Logf("%d categories with distinct non-null VAT: %d actual SQL queries", size, len(queries))
	}
}

func paymentCategoryMutationSnapshot(t *testing.T, db *sqlx.DB) map[string]string {
	t.Helper()
	result := map[string]string{}
	for _, table := range []string{"application_paymentcategory", "application_vatmanagement", "auditlog_logentry", "application_auditlogindex"} {
		var records string
		if err := db.Get(&records, `SELECT COALESCE(jsonb_agg(row_data ORDER BY row_data::text),'[]'::jsonb)::text FROM (SELECT to_jsonb(t) AS row_data FROM `+table+` t) rows`); err != nil {
			t.Fatal(err)
		}
		result[table] = records
	}
	return result
}

func paymentCategoryAwaitDB(t *testing.T, description string, condition func() (bool, error)) {
	t.Helper()
	deadline := time.NewTimer(10 * time.Second)
	defer deadline.Stop()
	tick := time.NewTicker(10 * time.Millisecond)
	defer tick.Stop()
	for {
		ok, err := condition()
		if err != nil {
			t.Fatal(description, err)
		}
		if ok {
			return
		}
		select {
		case <-deadline.C:
			t.Fatal("PostgreSQL did not reach observed state:", description)
		case <-tick.C:
		}
	}
}

func TestPaymentCategoryBlockedCancellationRecoveryLive(t *testing.T) {
	db := openPaymentCategoryIntegrationDB(t)
	categoryID, sentinelID := "19300000-0000-4000-8000-000000000501", "19300000-0000-4000-8000-000000000502"
	paymentCategoryOwnedRowsCleanup(t, db, []string{categoryID, sentinelID}, nil)
	association, foreign := paymentCategoryDBAssociation, paymentCategoryDBForeignAssoc
	vat := paymentCategoryDBVAT
	insertPaymentCategoryIntegration(t, db, categoryID, "before cancellation", &association, time.Now().UTC(), false, &vat)
	insertPaymentCategoryIntegration(t, db, sentinelID, "unrelated foreign sentinel", &foreign, time.Now().UTC(), false, &vat)
	before := paymentCategoryMutationSnapshot(t, db)
	blocker, err := db.Beginx()
	if err != nil {
		t.Fatal(err)
	}
	defer blocker.Rollback()
	var blockerPID int
	if err = blocker.Get(&blockerPID, `SELECT pg_backend_pid()`); err != nil {
		t.Fatal(err)
	}
	var lockedID string
	if err = blocker.Get(&lockedID, `SELECT payment_category_id FROM application_paymentcategory WHERE payment_category_id=$1 FOR UPDATE`, categoryID); err != nil {
		t.Fatal(err)
	}
	trace := &paymentCategoryQueryTrace{categoryLock: make(chan uint32, 1)}
	workerDB := paymentCategoryTracedDB(t, trace)
	flow := NewPaymentCategories(repository.NewPaymentCategories(workerDB), time.Now)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	result := make(chan error, 1)
	go func() {
		_, err := flow.Update(ctx, paymentCategoryDBIdentity(), categoryID,
			dto.PaymentCategoryWrite{NamePresent: true, Name: stringPointer("must not survive")}, domain.LoginAuditContext{})
		result <- err
	}()
	var waiterPID uint32
	select {
	case waiterPID = <-trace.categoryLock:
	case <-time.After(10 * time.Second):
		t.Fatal("worker did not issue category-lock SQL")
	}
	paymentCategoryAwaitDB(t, "category SQL blocked by held fixture transaction", func() (bool, error) {
		var waiting bool
		err := db.Get(&waiting, `SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=$1 AND state='active' AND wait_event_type='Lock' AND $2::int=ANY(pg_blocking_pids(pid)))`, waiterPID, blockerPID)
		return waiting, err
	})
	cancel()
	select {
	case err = <-result:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("blocked query cancellation returned %v", err)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("canceled mutation did not complete while category lock remained held")
	}
	// Observe server cleanup before releasing the original lock. Merely returning
	// context.Canceled or returning the connection to database/sql is insufficient.
	paymentCategoryAwaitDB(t, "canceled backend transaction and SQL ended", func() (bool, error) {
		var alive bool
		err := db.Get(&alive, `SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=$1 AND (xact_start IS NOT NULL OR state='active' OR wait_event_type='Lock'))`, waiterPID)
		return !alive, err
	})
	if got := paymentCategoryMutationSnapshot(t, db); !reflect.DeepEqual(got, before) {
		t.Fatal("cancellation changed category, VAT, audit, index or unrelated rows")
	}
	if workerDB.Stats().InUse != 0 {
		t.Fatal("canceled mutation retained a database/sql connection")
	}
	if err = blocker.Rollback(); err != nil {
		t.Fatal(err)
	}
	recovery, stopRecovery := context.WithTimeout(context.Background(), 10*time.Second)
	defer stopRecovery()
	row, err := flow.Update(recovery, paymentCategoryDBIdentity(), categoryID,
		dto.PaymentCategoryWrite{NamePresent: true, Name: stringPointer("recovered")}, domain.LoginAuditContext{})
	if err != nil || row.Name != "recovered" || row.VAT == nil || row.VAT.ID != vat {
		t.Fatal("recovery failed after cancellation", row, err)
	}
	var auditCount, indexCount int
	if err = db.Get(&auditCount, `SELECT count(*) FROM auditlog_logentry WHERE object_pk=$1 AND changes='{"name":["before cancellation","recovered"]}'::jsonb`, categoryID); err != nil || auditCount != 1 {
		t.Fatal("recovery audit", auditCount, err)
	}
	if err = db.Get(&indexCount, `SELECT count(*) FROM application_auditlogindex i JOIN auditlog_logentry l ON l.id=i.log_entry_id WHERE l.object_pk=$1 AND i.sport_association_id=$2`, categoryID, association); err != nil || indexCount != 1 {
		t.Fatal("recovery index", indexCount, err)
	}
	var sentinelName string
	if err = db.Get(&sentinelName, `SELECT name FROM application_paymentcategory WHERE payment_category_id=$1`, sentinelID); err != nil || sentinelName != "unrelated foreign sentinel" {
		t.Fatal("recovery changed unrelated record", sentinelName, err)
	}
	t.Logf("PostgreSQL waiter %d observed blocked by %d; canceled and ended before lock release; audited recovery succeeded", waiterPID, blockerPID)
}
