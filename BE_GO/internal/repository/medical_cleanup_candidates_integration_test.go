//go:build medicalcleanupintegration

package repository

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
	"github.com/google/uuid"
	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

type medicalCandidateQueries struct {
	*sqlx.DB
	count atomic.Int64
	mu    sync.Mutex
	query string
	args  []any
}

func (q *medicalCandidateQueries) QueryxContext(ctx context.Context, query string, args ...any) (*sqlx.Rows, error) {
	q.count.Add(1)
	q.mu.Lock()
	q.query = query
	q.args = append([]any(nil), args...)
	q.mu.Unlock()
	return q.DB.QueryxContext(ctx, query, args...)
}
func TestMedicalCleanupCandidatesPostgres(t *testing.T) {
	var fixture struct {
		Expected []struct {
			ID           string
			Created      time.Time
			Filename     string
			Filepath     *string
			Size         *int64
			Certificates int64
		}
		Protected, Scaled   []string
		ScaleAfter, Invoice string
	}
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_MEDICAL_CANDIDATES_FIXTURE"))
	if err != nil || json.Unmarshal(raw, &fixture) != nil || len(fixture.Expected) != 3 || len(fixture.Protected) != 19 || len(fixture.Scaled) != 2048 {
		t.Fatal("run accounts.py --only medical-orphan-cleanup")
	}
	db, err := sqlx.Connect("pgx", os.Getenv("ASSOZETA_MEDICAL_CANDIDATES_DSN"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	db.SetMaxOpenConns(4)
	q := &medicalCandidateQueries{DB: db}
	reader := NewMedicalCleanupCandidates(q)
	ctx, stop := context.WithTimeout(context.Background(), 90*time.Second)
	defer stop()
	scaleAfter := uuid.MustParse(fixture.ScaleAfter)
	t.Run("all document references and historical drafts protect candidates", func(t *testing.T) {
		before := q.count.Load()
		got, e := reader.ReadMedicalCleanupCandidates(ctx, nil, 512)
		if e != nil || len(got) != 512 || q.count.Load()-before != 1 {
			t.Fatal("candidate batch", len(got), e)
		}
		for i, row := range fixture.Expected {
			actual := got[i]
			if actual.ID.String() != row.ID || !actual.Created.Equal(row.Created) || actual.Filename != row.Filename || !reflect.DeepEqual(actual.Filepath, row.Filepath) || !reflect.DeepEqual(actual.Size, row.Size) || actual.Certificates != row.Certificates {
				t.Fatalf("wrong candidate metadata: %+v", actual)
			}
		}
		for i, row := range got[3:] {
			if row.ID.String() != fixture.Scaled[i] {
				t.Fatal("protected document exposed", row.ID)
			}
		}
	})
	t.Run("page limits cancellation and zero UUID cursor", func(t *testing.T) {
		before := q.count.Load()
		for _, limit := range []int{-1, 0, 513} {
			got, e := reader.ReadMedicalCleanupCandidates(ctx, nil, limit)
			if got != nil || !errors.Is(e, domain.ErrMedicalCleanupPage) || q.count.Load() != before {
				t.Fatal("invalid page reached SQL", limit, e)
			}
		}
		canceled, cancel := context.WithCancel(ctx)
		cancel()
		if got, e := reader.ReadMedicalCleanupCandidates(canceled, nil, 1); got != nil || !errors.Is(e, context.Canceled) || q.count.Load() != before {
			t.Fatal("canceled read reached SQL", e)
		}
		var cursor *uuid.UUID
		for _, expected := range fixture.Expected {
			got, e := reader.ReadMedicalCleanupCandidates(ctx, cursor, 1)
			if e != nil || len(got) != 1 || got[0].ID.String() != expected.ID {
				t.Fatal("cursor skipped zero/eligible UUID", expected.ID, e)
			}
			id := got[0].ID
			cursor = &id
		}
	})
	t.Run("caller transaction sees new document references", func(t *testing.T) {
		tx, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer tx.Rollback()
		if _, e = tx.ExecContext(ctx, `INSERT INTO application_invoice_attached_documents (invoice_id,document_id) VALUES ($1,$2)`, fixture.Invoice, fixture.Expected[0].ID); e != nil {
			t.Fatal(e)
		}
		got, e := NewMedicalCleanupCandidates(tx).ReadMedicalCleanupCandidates(ctx, nil, 1)
		if e != nil || len(got) != 1 || got[0].ID.String() != fixture.Expected[1].ID {
			t.Fatal("transaction reference ignored", e)
		}
	})
	t.Run("one statement across scale and concurrent reads", func(t *testing.T) {
		for _, limit := range []int{1, 16, 128, 512} {
			before := q.count.Load()
			got, e := reader.ReadMedicalCleanupCandidates(ctx, &scaleAfter, limit)
			if e != nil || len(got) != limit || q.count.Load()-before != 1 {
				t.Fatal("scaled read", limit, len(got), e)
			}
			for i, row := range got {
				if row.ID.String() != fixture.Scaled[i] || row.Certificates != 1 {
					t.Fatal("draft-protected scaled row exposed", i, row.ID)
				}
			}
		}
		before := q.count.Load()
		var wg sync.WaitGroup
		for i := 0; i < 32; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				got, e := reader.ReadMedicalCleanupCandidates(ctx, &scaleAfter, 16)
				if e != nil || len(got) != 16 {
					t.Error("concurrent read", e)
				}
			}()
		}
		wg.Wait()
		if q.count.Load()-before != 32 || db.Stats().InUse != 0 || db.Stats().MaxOpenConnections != 4 {
			t.Fatal("query/pool bounds")
		}
	})
	t.Run("blocked query cancellation and pool recovery", func(t *testing.T) {
		blocker, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer blocker.Rollback()
		if _, e = blocker.ExecContext(ctx, `LOCK TABLE docmanager_document IN ACCESS EXCLUSIVE MODE`); e != nil {
			t.Fatal(e)
		}
		var pid int
		if e = blocker.GetContext(ctx, &pid, `SELECT pg_backend_pid()`); e != nil {
			t.Fatal(e)
		}
		readCtx, cancel := context.WithCancel(ctx)
		defer cancel()
		done := make(chan error, 1)
		go func() {
			rows, e := reader.ReadMedicalCleanupCandidates(readCtx, nil, 1)
			if rows != nil {
				e = errors.New("canceled read exposed rows")
			}
			done <- e
		}()
		blocked := false
		until := time.Now().Add(3 * time.Second)
		for !blocked && time.Now().Before(until) {
			if e = db.GetContext(ctx, &blocked, `SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock' AND $1=ANY(pg_blocking_pids(pid)))`, pid); e != nil {
				break
			}
			if !blocked {
				time.Sleep(5 * time.Millisecond)
			}
		}
		cancel()
		readErr := <-done
		_ = blocker.Rollback()
		if !blocked || e != nil || !errors.Is(readErr, context.Canceled) {
			t.Fatal("lock cancellation", blocked, e, readErr)
		}
		got, e := reader.ReadMedicalCleanupCandidates(ctx, nil, 1)
		if e != nil || len(got) != 1 || db.Stats().InUse != 0 {
			t.Fatal("pool did not recover", e)
		}
	})
	t.Run("executed plan with mixed protected documents", func(t *testing.T) {
		if _, e := db.ExecContext(ctx, `ANALYZE docmanager_document,application_medicalcertificate,application_subscription,application_associateimportdraft`); e != nil {
			t.Fatal(e)
		}
		got, e := reader.ReadMedicalCleanupCandidates(ctx, &scaleAfter, 128)
		if e != nil || len(got) != 128 {
			t.Fatal(e)
		}
		q.mu.Lock()
		query, args := q.query, append([]any(nil), q.args...)
		q.mu.Unlock()
		var plan string
		if e = db.GetContext(ctx, &plan, "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) "+query, args...); e != nil {
			t.Fatal(e)
		}
		if e = os.WriteFile(os.Getenv("ASSOZETA_MEDICAL_CANDIDATES_PLAN"), []byte(plan), 0600); e != nil {
			t.Fatal(e)
		}
		var decoded []struct{ Plan map[string]any }
		if json.Unmarshal([]byte(plan), &decoded) != nil || len(decoded) != 1 || decoded[0].Plan["Actual Rows"] != float64(128) {
			t.Fatal("plan did not execute page")
		}
		indexed := false
		var visit func(map[string]any)
		visit = func(node map[string]any) {
			kind, _ := node["Node Type"].(string)
			if node["Relation Name"] == "docmanager_document" && strings.Contains(kind, "Index") && node["Actual Rows"].(float64) >= 128 {
				indexed = true
			}
			if children, ok := node["Plans"].([]any); ok {
				for _, child := range children {
					visit(child.(map[string]any))
				}
			}
		}
		visit(decoded[0].Plan)
		if !indexed {
			t.Fatal("document page did not use existing index")
		}
	})
}
