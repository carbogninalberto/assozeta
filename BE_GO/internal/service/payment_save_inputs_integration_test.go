//go:build paymentsaveinputsintegration

package service

import (
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
	"github.com/google/uuid"
	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

type paymentInputQueries struct {
	*sqlx.DB
	count atomic.Int64
	mu    sync.Mutex
	query string
	args  []any
}

func (q *paymentInputQueries) QueryxContext(ctx context.Context, sql string, args ...any) (*sqlx.Rows, error) {
	q.count.Add(1)
	q.mu.Lock()
	q.query = sql
	q.args = append([]any(nil), args...)
	q.mu.Unlock()
	return q.DB.QueryxContext(ctx, sql, args...)
}
func TestPaymentSaveInputsPostgres(t *testing.T) {
	var fixture struct {
		Valid []struct {
			Name, ID, Association, Category string
			OldCategory                     *string
			Description                     *string
			Invoice                         *string
			Archived, InvoiceArchived       bool
		}
		Denied                 []struct{ Name, ID, Association string }
		Malformed, Association string
		Scaled                 []string
	}
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_PAYMENT_INPUTS_FIXTURE"))
	if err != nil || json.Unmarshal(raw, &fixture) != nil || len(fixture.Valid) != 12 || len(fixture.Denied) != 11 || len(fixture.Scaled) != 4096 {
		t.Fatal("run accounts.py --only payment-save-inputs; complete fixture required")
	}
	db, err := sqlx.Connect("pgx", os.Getenv("ASSOZETA_PAYMENT_INPUTS_DSN"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	db.SetMaxOpenConns(4)
	queries := &paymentInputQueries{DB: db}
	prepare := NewPaymentSavePreparation(repository.NewPaymentSaveInputs(queries))
	ref := func(id, association string) domain.PaymentSaveReference {
		return domain.PaymentSaveReference{PaymentID: uuid.MustParse(id), AssociationID: uuid.MustParse(association)}
	}
	refs := make([]domain.PaymentSaveReference, len(fixture.Valid))
	for i, row := range fixture.Valid {
		refs[i] = ref(row.ID, row.Association)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	t.Run("one batch reproduces actual model descriptions and owned defaults", func(t *testing.T) {
		before := queries.count.Load()
		got, err := prepare.Prepare(ctx, refs)
		if err != nil || len(got) != len(refs) || queries.count.Load()-before != 1 {
			t.Fatalf("batch %d/%v queries=%d", len(got), err, queries.count.Load()-before)
		}
		for i, row := range fixture.Valid {
			p := got[i]
			optionalID := func(id *uuid.UUID) *string {
				if id == nil {
					return nil
				}
				s := id.String()
				return &s
			}
			if p.Payment.ID.String() != row.ID || p.Payment.AssociationID.String() != row.Association || !reflect.DeepEqual(p.Description, row.Description) || p.Payment.CategoryID == nil || p.Payment.CategoryID.String() != row.Category || !reflect.DeepEqual(optionalID(p.Payment.OriginalCategoryID), row.OldCategory) || p.Payment.Archived != row.Archived {
				t.Fatalf("%s: wrong payment snapshot/defaults %+v", row.Name, p)
			}
			if row.Invoice == nil {
				if p.Payment.Invoice != nil {
					t.Fatal("invented invoice", row.Name)
				}
			} else if p.Payment.Invoice == nil || p.Payment.Invoice.ID.String() != *row.Invoice || p.Payment.Invoice.Archived != row.InvoiceArchived {
				t.Fatal("wrong invoice state", row.Name)
			}
		}
	})
	t.Run("foreign and missing references fail the whole batch", func(t *testing.T) {
		for _, row := range fixture.Denied {
			before := queries.count.Load()
			got, err := prepare.Prepare(ctx, []domain.PaymentSaveReference{refs[0], ref(row.ID, row.Association)})
			if got != nil || !errors.Is(err, domain.ErrPaymentSaveScope) || queries.count.Load()-before != 1 {
				t.Fatalf("%s: partial/foreign data %v/%v", row.Name, got, err)
			}
		}
	})
	t.Run("batch bounds duplicate scope and nil UUID", func(t *testing.T) {
		before := queries.count.Load()
		got, err := prepare.Prepare(ctx, nil)
		if err != nil || len(got) != 0 || queries.count.Load() != before {
			t.Fatal("empty batch queried", err)
		}
		tooMany := make([]domain.PaymentSaveReference, 513)
		if got, err = prepare.Prepare(ctx, tooMany); got != nil || !errors.Is(err, domain.ErrPaymentSaveBatch) || queries.count.Load() != before {
			t.Fatal("oversized batch reached SQL", err)
		}
		mismatch := refs[0]
		mismatch.AssociationID = uuid.New()
		if got, err = prepare.Prepare(ctx, []domain.PaymentSaveReference{refs[0], mismatch}); got != nil || !errors.Is(err, domain.ErrPaymentSaveBatch) || queries.count.Load() != before {
			t.Fatal("conflicting ownership reached SQL", err)
		}
		got, err = prepare.Prepare(ctx, []domain.PaymentSaveReference{refs[0], refs[0]})
		if err != nil || len(got) != 1 || got[0].Payment.ID != uuid.Nil || queries.count.Load()-before != 1 {
			t.Fatal("valid zero UUID or duplicate semantics lost", err)
		}
	})
	t.Run("description failure does not return partial plans", func(t *testing.T) {
		got, err := prepare.Prepare(ctx, []domain.PaymentSaveReference{refs[0], ref(fixture.Malformed, fixture.Association)})
		if got != nil || !errors.Is(err, domain.ErrPaymentDescription) {
			t.Fatalf("invalid metadata accepted %v/%v", got, err)
		}
	})
	t.Run("caller transaction sees its changes and missing default", func(t *testing.T) {
		tx, err := db.BeginTxx(ctx, nil)
		if err != nil {
			t.Fatal(err)
		}
		defer tx.Rollback()
		if _, err = tx.ExecContext(ctx, `UPDATE application_paymentcategory SET name='not a default'`); err != nil {
			t.Fatal(err)
		}
		prepared, err := NewPaymentSavePreparation(repository.NewPaymentSaveInputs(tx)).Prepare(ctx, refs[:1])
		if err != nil || len(prepared) != 1 || prepared[0].Payment.CategoryID != nil || prepared[0].Description == nil || *prepared[0].Description != "Iscrizione" {
			t.Fatal("transaction/default mismatch", err)
		}
	})
	t.Run("constant queries and bounded pool under concurrent reads", func(t *testing.T) {
		for _, size := range []int{1, 16, 128, 512} {
			batch := make([]domain.PaymentSaveReference, size)
			for i := range batch {
				batch[i] = ref(fixture.Scaled[i], fixture.Association)
			}
			before := queries.count.Load()
			got, err := prepare.Prepare(ctx, batch)
			if err != nil || len(got) != size || queries.count.Load()-before != 1 {
				t.Fatalf("size %d: %d/%v", size, len(got), err)
			}
			for _, p := range got {
				if p.Description == nil || *p.Description != "Corso scala" {
					t.Fatal("scaled read omitted description")
				}
			}
		}
		before := queries.count.Load()
		var wg sync.WaitGroup
		for i := 0; i < 32; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				got, err := prepare.Prepare(ctx, refs[:1])
				if err != nil || len(got) != 1 {
					t.Error("concurrent read", err)
				}
			}()
		}
		wg.Wait()
		if queries.count.Load()-before != 32 || db.Stats().InUse != 0 || db.Stats().MaxOpenConnections != 4 {
			t.Fatal("query/pool bound violated")
		}
	})
	t.Run("blocked query cancellation and connection recovery", func(t *testing.T) {
		blocker, err := db.BeginTxx(ctx, nil)
		if err != nil {
			t.Fatal(err)
		}
		defer blocker.Rollback()
		if _, err = blocker.ExecContext(ctx, `LOCK TABLE application_payment IN ACCESS EXCLUSIVE MODE`); err != nil {
			t.Fatal(err)
		}
		var pid int
		if err = blocker.GetContext(ctx, &pid, `SELECT pg_backend_pid()`); err != nil {
			t.Fatal(err)
		}
		readCtx, stop := context.WithCancel(ctx)
		defer stop()
		done := make(chan error, 1)
		go func() {
			got, err := prepare.Prepare(readCtx, refs[:1])
			if got != nil {
				err = errors.New("canceled query exposed rows")
			}
			done <- err
		}()
		blocked := false
		until := time.Now().Add(5 * time.Second)
		for !blocked && time.Now().Before(until) {
			if err = db.GetContext(ctx, &blocked, `SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock' AND $1=ANY(pg_blocking_pids(pid)))`, pid); err != nil {
				break
			}
			if !blocked {
				time.Sleep(5 * time.Millisecond)
			}
		}
		stop()
		readErr := <-done
		_ = blocker.Rollback()
		if !blocked || err != nil || !errors.Is(readErr, context.Canceled) {
			t.Fatalf("blocked cancel %v/%v/%v", blocked, err, readErr)
		}
		got, err := prepare.Prepare(ctx, refs[:1])
		if err != nil || len(got) != 1 || db.Stats().InUse != 0 {
			t.Fatal("reader did not recover", err)
		}
	})
	t.Run("executed plan over existing table indexes", func(t *testing.T) {
		if _, err := db.ExecContext(ctx, `ANALYZE application_payment,application_coursesubscription,application_course,application_paymentcategory,application_subscription`); err != nil {
			t.Fatal(err)
		}
		batch := make([]domain.PaymentSaveReference, 128)
		for i := range batch {
			batch[i] = ref(fixture.Scaled[i*len(fixture.Scaled)/len(batch)], fixture.Association)
		}
		got, err := prepare.Prepare(ctx, batch)
		if err != nil || len(got) != 128 {
			t.Fatal(err)
		}
		queries.mu.Lock()
		query, args := queries.query, append([]any(nil), queries.args...)
		queries.mu.Unlock()
		var plan string
		if err = db.GetContext(ctx, &plan, "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) "+query, args...); err != nil {
			t.Fatal(err)
		}
		var decoded []struct{ Plan map[string]any }
		if json.Unmarshal([]byte(plan), &decoded) != nil || len(decoded) != 1 || decoded[0].Plan["Actual Rows"] != float64(128) {
			t.Fatal("plan did not execute expected batch")
		}
		indexedRelation := false
		var inspect func(map[string]any)
		inspect = func(node map[string]any) {
			condition, _ := node["Index Cond"].(string)
			if node["Relation Name"] == "application_coursesubscription" && strings.Contains(condition, "payment_id") && node["Actual Loops"] == float64(128) {
				indexedRelation = true
			}
			if children, ok := node["Plans"].([]any); ok {
				for _, child := range children {
					inspect(child.(map[string]any))
				}
			}
		}
		inspect(decoded[0].Plan)
		if !indexedRelation {
			t.Fatal("relation lookup did not use its payment foreign-key index across the full data range")
		}
		if err = os.WriteFile(os.Getenv("ASSOZETA_PAYMENT_INPUTS_PLAN"), []byte(plan), 0600); err != nil {
			t.Fatal(err)
		}
	})
}
