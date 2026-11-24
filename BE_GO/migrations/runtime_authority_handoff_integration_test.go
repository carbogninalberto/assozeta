//go:build jobsintegration

package migrations

// Phase 1 handoff contract: one durable runtime authority (effective runtime,
// generation and last committed operation ID) on the existing ownership row,
// changed only through one compare-and-set SQL function that the deployment
// controller calls. Real PostgreSQL; deterministic lock-wait barriers, never
// sleeps, decide which side of a commit each case exercises.
//
// Contract pinned here (migration 000005_runtime_authority):
//   - public.assozeta_go_job_ownership gains operation_id uuid NULL and
//     committed_at timestamptz NULL. Upgrading keeps runtime and generation.
//   - public.assozeta_commit_runtime_authority(p_operation_id uuid,
//     p_expected_runtime text, p_expected_generation bigint, p_target text)
//     RETURNS TABLE(outcome text, runtime text, generation bigint, operation_id uuid)
//     outcome 'committed': row matched (expected runtime, generation); it now
//       holds target, generation+1, the operation ID and committed_at.
//     outcome 'already_committed': the row already records this operation ID;
//       nothing changes (a retried commit never increments twice).
//     outcome 'conflict': the row did not match; nothing changes and the
//       current authority is returned to the caller.
//     SQLSTATE 22023 for an unknown target, target equal to the expected
//       runtime (a no-op is decided by the controller, never committed) or a
//       NULL operation ID.
//   - migrations.CurrentVersion is 5 and the down script refuses.
// Run: python TESTS/adapters/go/handoff_authority.py (private cluster, -race).

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgconn"
	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

type handoffAuthority struct {
	Runtime     string        `db:"runtime"`
	Generation  int64         `db:"generation"`
	OperationID uuid.NullUUID `db:"operation_id"`
}

type handoffCommit struct {
	Outcome     string        `db:"outcome"`
	Runtime     string        `db:"runtime"`
	Generation  int64         `db:"generation"`
	OperationID uuid.NullUUID `db:"operation_id"`
}

const handoffCommitSQL = `SELECT outcome, runtime, generation, operation_id
 FROM public.assozeta_commit_runtime_authority($1::uuid, $2::text, $3::bigint, $4::text)`

type handoffQueryer interface {
	GetContext(context.Context, any, string, ...any) error
}

func handoffCommitAuthority(ctx context.Context, q handoffQueryer, operation uuid.UUID, runtime string, generation int64, target string) (handoffCommit, error) {
	var result handoffCommit
	err := q.GetContext(ctx, &result, handoffCommitSQL, operation, runtime, generation, target)
	if err != nil && strings.Contains(err.Error(), "assozeta_commit_runtime_authority") && strings.Contains(err.Error(), "does not exist") {
		err = fmt.Errorf("CONTRACT MISSING: public.assozeta_commit_runtime_authority (migration 000005_runtime_authority): %w", err)
	}
	return result, err
}

func TestHandoffRuntimeAuthorityPostgres(t *testing.T) {
	dsn := os.Getenv("ASSOZETA_HANDOFF_AUTHORITY_DSN")
	if dsn == "" {
		t.Fatal("private PostgreSQL fixture required (python TESTS/adapters/go/handoff_authority.py); never skipped")
	}
	db, err := sqlx.Connect("pgx", dsn)
	if err != nil {
		t.Fatal("private database unavailable")
	}
	defer db.Close()
	db.SetMaxOpenConns(32)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	execute := func(t *testing.T, query string, args ...any) {
		t.Helper()
		if _, e := db.ExecContext(ctx, query, args...); e != nil {
			t.Fatal(e)
		}
	}
	read := func(t *testing.T) handoffAuthority {
		t.Helper()
		var row handoffAuthority
		if e := db.GetContext(ctx, &row, `SELECT runtime, generation, operation_id FROM public.assozeta_go_job_ownership WHERE singleton=1`); e != nil {
			t.Fatal("authority row unreadable (operation_id column is part of the contract):", e)
		}
		return row
	}
	// Trusted fixture state only; production changes go through the function.
	reset := func(t *testing.T, runtime string, generation int64) {
		t.Helper()
		if _, e := db.ExecContext(ctx, `UPDATE public.assozeta_go_job_ownership SET runtime=$1, generation=$2, operation_id=NULL, committed_at=NULL WHERE singleton=1`, runtime, generation); e != nil {
			t.Fatal("CONTRACT MISSING: operation_id/committed_at columns (migration 000005_runtime_authority):", e)
		}
	}
	mustCommit := func(t *testing.T, operation uuid.UUID, runtime string, generation int64, target string) handoffCommit {
		t.Helper()
		result, e := handoffCommitAuthority(ctx, db, operation, runtime, generation, target)
		if e != nil {
			t.Fatal(e)
		}
		return result
	}
	same := func(id uuid.NullUUID, want uuid.UUID) bool { return id.Valid && id.UUID == want }
	// waitForLockWait is the barrier: the competing statement is provably
	// blocked on a row lock (not merely started) before the test proceeds.
	waitForLockWait := func(t *testing.T, fragment string) {
		t.Helper()
		deadline := time.Now().Add(10 * time.Second)
		for {
			var waiting bool
			if e := db.GetContext(ctx, &waiting, `SELECT EXISTS(SELECT 1 FROM pg_stat_activity
 WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE '%' || $1 || '%')`, fragment); e != nil {
				t.Fatal(e)
			}
			if waiting {
				return
			}
			if time.Now().After(deadline) {
				t.Fatal("competing statement never waited on the authority row lock:", fragment)
			}
			time.Sleep(time.Millisecond)
		}
	}

	t.Run("upgrade from version 4 keeps ownership and adds operation identity", func(t *testing.T) {
		// Reconstruct an installed version-4 operational schema, owned by Go at 7.
		for _, name := range []string{"000001_durable_jobs.up.sql", "000002_job_ownership.up.sql",
			"000003_periodic_occurrences.up.sql", "000004_realtime_events.up.sql"} {
			body, e := files.ReadFile(name)
			if e != nil {
				t.Fatal(e)
			}
			execute(t, string(body))
		}
		execute(t, `CREATE TABLE assozeta_go_schema_migrations(version bigint PRIMARY KEY, dirty boolean NOT NULL);
 INSERT INTO assozeta_go_schema_migrations VALUES (4, false);
 UPDATE public.assozeta_go_job_ownership SET runtime='go', generation=7 WHERE singleton=1`)
		if CurrentVersion < 5 {
			t.Fatalf("CONTRACT MISSING: migration 000005_runtime_authority (CurrentVersion=%d, want >= 5)", CurrentVersion)
		}
		if e := Apply(dsn); e != nil {
			t.Fatal(e)
		}
		var version uint
		// Later migrations (000006_job_handoff, Phase 3) apply on top; the upgrade lands on the binary's version.
		if e := db.GetContext(ctx, &version, `SELECT version FROM assozeta_go_schema_migrations WHERE NOT dirty`); e != nil || version != CurrentVersion {
			t.Fatal("operational schema is not at the current version", version, CurrentVersion, e)
		}
		row := read(t)
		if row.Runtime != "go" || row.Generation != 7 || row.OperationID.Valid {
			t.Fatalf("upgrade changed the authority: %+v", row)
		}
		var columns []string
		if e := db.SelectContext(ctx, &columns, `SELECT column_name || ':' || data_type || ':' || is_nullable FROM information_schema.columns
 WHERE table_schema='public' AND table_name='assozeta_go_job_ownership' AND column_name IN ('operation_id','committed_at') ORDER BY column_name`); e != nil {
			t.Fatal(e)
		}
		if strings.Join(columns, ",") != "committed_at:timestamp with time zone:YES,operation_id:uuid:YES" {
			t.Fatalf("authority columns differ from the contract: %v", columns)
		}
		if e := Apply(dsn); e != nil {
			t.Fatal("reapplying failed", e)
		}
		if again := read(t); again != row {
			t.Fatalf("reapplying changed the authority: %+v", again)
		}
		down, e := files.ReadFile("000005_runtime_authority.down.sql")
		if e != nil {
			t.Fatal("CONTRACT MISSING: 000005_runtime_authority.down.sql must exist and refuse", e)
		}
		if _, e = db.ExecContext(ctx, string(down)); e == nil {
			t.Fatal("destructive down migration accepted")
		}
		if after := read(t); after != row {
			t.Fatalf("down refusal changed the authority: %+v", after)
		}
	})

	t.Run("one commit advances generation and records the operation", func(t *testing.T) {
		reset(t, "django", 1)
		operation := uuid.New()
		result := mustCommit(t, operation, "django", 1, "go")
		if result.Outcome != "committed" || result.Runtime != "go" || result.Generation != 2 || !same(result.OperationID, operation) {
			t.Fatalf("commit result %+v", result)
		}
		row := read(t)
		if row.Runtime != "go" || row.Generation != 2 || !same(row.OperationID, operation) {
			t.Fatalf("stored authority %+v", row)
		}
		var stamped bool
		if e := db.GetContext(ctx, &stamped, `SELECT committed_at IS NOT NULL AND committed_at <= clock_timestamp() FROM public.assozeta_go_job_ownership`); e != nil || !stamped {
			t.Fatal("committed_at not recorded", e)
		}
	})

	t.Run("retrying the committed operation never commits twice", func(t *testing.T) {
		reset(t, "django", 1)
		operation := uuid.New()
		mustCommit(t, operation, "django", 1, "go")
		// A timeout retry repeats the original arguments (expected django@1).
		for range 3 {
			result := mustCommit(t, operation, "django", 1, "go")
			if result.Outcome != "already_committed" || result.Runtime != "go" || result.Generation != 2 || !same(result.OperationID, operation) {
				t.Fatalf("retry result %+v", result)
			}
		}
		if row := read(t); row.Generation != 2 || row.Runtime != "go" || !same(row.OperationID, operation) {
			t.Fatalf("retry changed the authority: %+v", row)
		}
	})

	t.Run("stale expected state is rejected with the current authority", func(t *testing.T) {
		reset(t, "django", 1)
		winner := uuid.New()
		mustCommit(t, winner, "django", 1, "go")
		before := read(t)
		for _, stale := range []struct {
			runtime    string
			generation int64
			target     string
		}{{"django", 1, "go"}, {"go", 1, "django"}, {"django", 2, "go"}, {"go", 3, "django"}} {
			result := mustCommit(t, uuid.New(), stale.runtime, stale.generation, stale.target)
			if result.Outcome != "conflict" || result.Runtime != "go" || result.Generation != 2 || !same(result.OperationID, winner) {
				t.Fatalf("stale %+v: result %+v", stale, result)
			}
		}
		if after := read(t); after != before {
			t.Fatalf("a rejected commit changed the authority: %+v", after)
		}
	})

	t.Run("invalid or no-op transitions are refused without change", func(t *testing.T) {
		reset(t, "django", 4)
		before := read(t)
		for _, bad := range []struct {
			operation *uuid.UUID
			runtime   string
			target    string
		}{{nil, "django", "go"}, {handoffPtr(uuid.New()), "django", "django"}, {handoffPtr(uuid.New()), "django", "node"}, {handoffPtr(uuid.New()), "django", ""}} {
			var arg any
			if bad.operation != nil {
				arg = *bad.operation
			}
			var result handoffCommit
			e := db.GetContext(ctx, &result, handoffCommitSQL, arg, bad.runtime, int64(4), bad.target)
			var pgErr *pgconn.PgError
			if !errors.As(e, &pgErr) || pgErr.Code != "22023" {
				t.Fatalf("%+v: want SQLSTATE 22023, got %v", bad, e)
			}
		}
		if after := read(t); after != before {
			t.Fatalf("a refused transition changed the authority: %+v", after)
		}
	})

	competing := func(t *testing.T, winnerCommits bool) {
		reset(t, "django", 1)
		first, second := uuid.New(), uuid.New()
		tx, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer tx.Rollback()
		if result, e := handoffCommitAuthority(ctx, tx, first, "django", 1, "go"); e != nil || result.Outcome != "committed" {
			t.Fatal("first commit", result, e)
		}
		type answer struct {
			result handoffCommit
			err    error
		}
		done := make(chan answer, 1)
		go func() {
			// The other owner's request is for the same source generation.
			result, e := handoffCommitAuthority(ctx, db, second, "django", 1, "go")
			done <- answer{result, e}
		}()
		waitForLockWait(t, "assozeta_commit_runtime_authority")
		select {
		case early := <-done:
			t.Fatalf("competing commit answered while the first held the authority: %+v", early)
		default:
		}
		if winnerCommits {
			if e = tx.Commit(); e != nil {
				t.Fatal(e)
			}
		} else if e = tx.Rollback(); e != nil {
			t.Fatal(e)
		}
		got := <-done
		if got.err != nil {
			t.Fatal(got.err)
		}
		row := read(t)
		if winnerCommits {
			if got.result.Outcome != "conflict" || got.result.Generation != 2 || !same(got.result.OperationID, first) {
				t.Fatalf("loser must see the winner's authority: %+v", got.result)
			}
			if row.Generation != 2 || !same(row.OperationID, first) {
				t.Fatalf("authority after winner: %+v", row)
			}
		} else {
			if got.result.Outcome != "committed" || got.result.Generation != 2 || !same(got.result.OperationID, second) {
				t.Fatalf("after a rolled-back commit the waiter must commit once: %+v", got.result)
			}
			if row.Generation != 2 || !same(row.OperationID, second) {
				t.Fatalf("authority after rollback: %+v", row)
			}
		}
	}
	t.Run("a competing commit waits and loses to a committed winner", func(t *testing.T) { competing(t, true) })
	t.Run("a competing commit waits and wins after the holder rolls back", func(t *testing.T) { competing(t, false) })

	t.Run("concurrent opposite requests produce exactly one winner", func(t *testing.T) {
		for round := range 5 {
			reset(t, "django", int64(10+round))
			generation := int64(10 + round)
			start := make(chan struct{})
			var wg sync.WaitGroup
			results := make([]handoffCommit, 16)
			errs := make([]error, 16)
			for i := range 16 {
				wg.Add(1)
				go func() {
					defer wg.Done()
					<-start
					// Even workers want Go from the observed Django state; odd workers
					// want Django from a Go state they believe current.
					runtime, target := "django", "go"
					if i%2 == 1 {
						runtime, target = "go", "django"
					}
					results[i], errs[i] = handoffCommitAuthority(ctx, db, uuid.New(), runtime, generation, target)
				}()
			}
			close(start)
			wg.Wait()
			winners := 0
			var winner handoffCommit
			for i, e := range errs {
				if e != nil {
					t.Fatal(e)
				}
				if results[i].Outcome == "committed" {
					winners++
					winner = results[i]
				} else if results[i].Outcome != "conflict" {
					t.Fatalf("unexpected outcome %+v", results[i])
				}
			}
			if winners != 1 || winner.Runtime != "go" || winner.Generation != generation+1 {
				t.Fatalf("round %d: %d winners, %+v", round, winners, winner)
			}
			for i, result := range results {
				if result.Outcome != "conflict" {
					continue
				}
				won := result.Runtime == "go" && result.Generation == generation+1 && same(result.OperationID, winner.OperationID.UUID)
				before := result.Runtime == "django" && result.Generation == generation && !result.OperationID.Valid
				// A loser asking for the same source can only lose after the winner
				// committed, so it must see the winner; a stale-runtime request may be
				// answered with either state that really existed, never another one.
				if (i%2 == 0 && !won) || (i%2 == 1 && !won && !before) {
					t.Fatalf("loser %d did not receive a real current authority: %+v", i, result)
				}
			}
			if row := read(t); row.Generation != generation+1 || !same(row.OperationID, winner.OperationID.UUID) {
				t.Fatalf("stored authority %+v", row)
			}
		}
	})

	t.Run("round trips keep generations strictly monotonic", func(t *testing.T) {
		reset(t, "django", 1)
		runtime, generation := "django", int64(1)
		seen := map[uuid.UUID]bool{}
		for range 20 {
			target := map[string]string{"django": "go", "go": "django"}[runtime]
			operation := uuid.New()
			result := mustCommit(t, operation, runtime, generation, target)
			if result.Outcome != "committed" || result.Generation != generation+1 || result.Runtime != target {
				t.Fatalf("round trip step %+v", result)
			}
			if seen[operation] {
				t.Fatal("operation reused")
			}
			seen[operation] = true
			runtime, generation = target, result.Generation
		}
		if row := read(t); row.Generation != 21 || row.Runtime != "django" {
			t.Fatalf("after 20 commits: %+v", row)
		}
	})

	t.Run("authority commit waits for admitted fenced work", func(t *testing.T) {
		reset(t, "go", 30)
		// The existing durable-job fence holds the row FOR SHARE for a whole
		// claim/effect transaction; ownership must not change underneath it.
		fenced, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer fenced.Rollback()
		if _, e = fenced.ExecContext(ctx, `SELECT generation FROM public.assozeta_go_job_ownership WHERE singleton=1 AND runtime='go' FOR SHARE`); e != nil {
			t.Fatal(e)
		}
		operation := uuid.New()
		done := make(chan error, 1)
		go func() {
			_, e := handoffCommitAuthority(ctx, db, operation, "go", 30, "django")
			done <- e
		}()
		waitForLockWait(t, "assozeta_commit_runtime_authority")
		if row := read(t); row.Runtime != "go" || row.Generation != 30 {
			t.Fatalf("authority changed while fenced work was admitted: %+v", row)
		}
		if e = fenced.Commit(); e != nil {
			t.Fatal(e)
		}
		if e = <-done; e != nil {
			t.Fatal(e)
		}
		if row := read(t); row.Runtime != "django" || row.Generation != 31 || !same(row.OperationID, operation) {
			t.Fatalf("authority after fenced work finished: %+v", row)
		}
	})
}

func handoffPtr(value uuid.UUID) *uuid.UUID { return &value }
