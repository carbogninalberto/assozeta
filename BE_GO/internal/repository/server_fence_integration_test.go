//go:build serverfenceintegration

package repository_test

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/migrations"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

type fenceTrace struct {
	sync.Mutex
	queries []string
}

func (s *fenceTrace) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	s.Lock()
	s.queries = append(s.queries, d.SQL)
	s.Unlock()
	return ctx
}
func (*fenceTrace) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}
func (s *fenceTrace) snapshot() []string {
	s.Lock()
	defer s.Unlock()
	return append([]string(nil), s.queries...)
}
func fenceMust(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}
func fenceAwait(t *testing.T, ctx context.Context, f func() bool) {
	t.Helper()
	for !f() {
		select {
		case <-ctx.Done():
			t.Fatal("private PostgreSQL condition timed out")
		case <-time.After(time.Millisecond):
		}
	}
}
func TestQueueServerFence(t *testing.T) {
	dsn := os.Getenv("ASSOZETA_SERVER_FENCE_DSN")
	cfg, err := pgx.ParseConfig(dsn)
	fenceMust(t, err)
	if cfg.Database != "assozeta_server_fence" || !strings.HasPrefix(cfg.Host, "/tmp/az-server-fence-") {
		t.Fatal("explicit private fixture database required")
	}
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	admin := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	defer admin.Close()
	fenceMust(t, migrations.Apply(dsn))
	_, err = admin.Exec(`UPDATE assozeta_go_job_ownership SET runtime='go',generation=2`)
	fenceMust(t, err)
	for _, mode := range []string{"claim", "renew", "complete", "fail", "cancel_pending"} {
		for _, scenario := range []string{"healthy_first_fence", "cancelled_first_fence_zero_effects", "late_commit_retains_fence"} {
			t.Run(mode+"/"+scenario, func(t *testing.T) {
				ctx, stop := context.WithTimeout(context.Background(), 8*time.Second)
				defer stop()
				_, err := admin.ExecContext(ctx, `DROP TRIGGER IF EXISTS server_fence_deferred ON assozeta_go_jobs; DROP FUNCTION IF EXISTS server_fence_deferred(); TRUNCATE assozeta_go_periodic_occurrences,assozeta_go_jobs`)
				fenceMust(t, err)
				id, token := uuid.New(), uuid.New()
				state, attempts, generation := "pending", 0, 0
				if mode != "claim" && mode != "cancel_pending" {
					state, attempts, generation = "running", 1, 2
				}
				_, err = admin.ExecContext(ctx, `INSERT INTO assozeta_go_jobs(id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at,state,attempts,lease_owner,lease_token,lease_until,ownership_generation) VALUES($1,$2,$3,1,$4,'{}',3,clock_timestamp()-interval '1 minute',$5,$6,CASE WHEN $5='running' THEN 'fixture' ELSE '' END,CASE WHEN $5='running' THEN $7::uuid ELSE '00000000-0000-0000-0000-000000000000'::uuid END,CASE WHEN $5='running' THEN clock_timestamp()+interval '10 seconds' ELSE NULL END,$8)`, id, uuid.Nil, domain.JobResetDailyEmailBalance, mode, state, attempts, token, generation)
				fenceMust(t, err)
				var before string
				fenceMust(t, admin.GetContext(ctx, &before, `SELECT to_jsonb(j)::text FROM assozeta_go_jobs j WHERE id=$1`, id))
				blocker, err := admin.Connx(ctx)
				fenceMust(t, err)
				local := cfg.Copy()
				trace := &fenceTrace{}
				local.Tracer = trace
				dial := local.DialFunc
				var failCancel atomic.Bool
				local.DialFunc = func(c context.Context, n, a string) (net.Conn, error) {
					if failCancel.Load() {
						return nil, errors.New("private fixture CancelRequest transport unavailable")
					}
					return dial(c, n, a)
				}
				db := sqlx.NewDb(stdlib.OpenDB(*local), "pgx")
				db.SetMaxOpenConns(1)
				db.SetMaxIdleConns(1)
				defer func() {
					_, _ = blocker.ExecContext(context.Background(), `SELECT pg_advisory_unlock_all()`)
					_ = blocker.Close()
					_ = db.Close()
				}()
				pinned, err := db.Connx(ctx)
				fenceMust(t, err)
				var cleanup <-chan struct{}
				var pid uint32
				fenceMust(t, pinned.Raw(func(raw any) error {
					p := raw.(interface{ Conn() *pgx.Conn }).Conn().PgConn()
					cleanup = p.CleanupDone()
					pid = p.PID()
					return nil
				}))
				fenceMust(t, pinned.Close())
				invoke := func(call context.Context) error {
					r := repository.NewJobs(db)
					switch mode {
					case "claim":
						j, e := r.Claim(call, []string{domain.JobResetDailyEmailBalance}, "fixture", 1, time.Minute)
						if e == nil && len(j) != 1 {
							return errors.New("expected one claimed job")
						}
						return e
					case "renew":
						return r.Renew(call, id, token, time.Minute)
					case "complete":
						return r.Complete(call, id, token, json.RawMessage(`{"done":true}`))
					case "fail":
						return r.Fail(call, id, token, "fixture.failure", false, 0)
					default:
						return r.CancelPending(call, uuid.Nil, id)
					}
				}
				mutated := func() bool {
					var after string
					fenceMust(t, admin.GetContext(ctx, &after, `SELECT to_jsonb(j)::text FROM assozeta_go_jobs j WHERE id=$1`, id))
					return after != before
				}
				if scenario == "healthy_first_fence" {
					fenceMust(t, invoke(ctx))
					if !mutated() {
						t.Fatal("successful mutation had no effect")
					}
					q := trace.snapshot()
					if len(q) != 4 || strings.ToLower(q[0]) != "begin" || !strings.Contains(q[1], "pg_advisory_xact_lock_shared") || strings.ToLower(q[3]) != "commit" {
						t.Fatalf("expected BEGIN, separate shared fence, effect, COMMIT; got %v", q)
					}
					var got string
					fenceMust(t, admin.GetContext(ctx, &got, `SELECT state FROM assozeta_go_jobs WHERE id=$1`, id))
					want := map[string]string{"claim": "running", "renew": "running", "complete": "completed", "fail": "failed", "cancel_pending": "cancelled"}[mode]
					if got != want {
						t.Fatalf("state %s, want %s", got, want)
					}
					return
				}
				if scenario == "cancelled_first_fence_zero_effects" {
					_, err = blocker.ExecContext(ctx, `SELECT pg_advisory_lock(1095981639,1381192786)`)
					fenceMust(t, err)
				} else {
					_, err = admin.ExecContext(ctx, `CREATE FUNCTION server_fence_deferred() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_advisory_xact_lock(837462901); RETURN NEW; END $$; CREATE CONSTRAINT TRIGGER server_fence_deferred AFTER UPDATE ON assozeta_go_jobs DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION server_fence_deferred()`)
					fenceMust(t, err)
					_, err = blocker.ExecContext(ctx, `SELECT pg_advisory_lock(837462901)`)
					fenceMust(t, err)
					failCancel.Store(true)
				}
				call, cancel := context.WithCancel(ctx)
				defer cancel()
				done := make(chan error, 1)
				go func() { done <- invoke(call) }()
				fenceAwait(t, ctx, func() bool {
					select {
					case e := <-done:
						t.Fatalf("mutation escaped blocked server fence: %v", e)
					default:
					}
					var waiting bool
					fenceMust(t, admin.GetContext(ctx, &waiting, `SELECT coalesce(bool_or(wait_event_type='Lock' AND wait_event='advisory'),false) FROM pg_stat_activity WHERE pid=$1`, pid))
					return waiting
				})
				cancel()
				select {
				case e := <-done:
					if e == nil {
						t.Fatal("cancelled mutation succeeded")
					}
				case <-ctx.Done():
					t.Fatal("cancelled caller did not finish")
				}
				if scenario == "cancelled_first_fence_zero_effects" {
					for _, q := range trace.snapshot() {
						if strings.Contains(strings.ToUpper(q), "UPDATE") {
							t.Fatalf("effect SQL sent while initial fence unavailable: %s", q)
						}
					}
					_, err = blocker.ExecContext(ctx, `SELECT pg_advisory_unlock_all()`)
					fenceMust(t, err)
					if mutated() {
						t.Fatal("cancelled initial waiter changed job")
					}
					return
				}
				select {
				case <-cleanup:
				case <-ctx.Done():
					t.Fatal("local connection cleanup did not finish")
				}
				drain, err := admin.BeginTxx(ctx, nil)
				fenceMust(t, err)
				var admitted bool
				fenceMust(t, drain.GetContext(ctx, &admitted, `SELECT pg_try_advisory_xact_lock(1095981639,1381192786)`))
				fenceMust(t, drain.Rollback())
				if admitted {
					t.Fatal("exclusive server drain admitted while remote mutation still committing")
				}
				if mutated() {
					t.Fatal("blocked deferred commit unexpectedly visible")
				}
				_, err = blocker.ExecContext(ctx, `SELECT pg_advisory_unlock_all()`)
				fenceMust(t, err)
				fenceAwait(t, ctx, mutated)
				drain, err = admin.BeginTxx(ctx, nil)
				fenceMust(t, err)
				_, err = drain.ExecContext(ctx, `SELECT pg_advisory_xact_lock(1095981639,1381192786)`)
				fenceMust(t, err)
				fenceMust(t, drain.Commit())
				if !mutated() {
					t.Fatal("post-drain committed state missing")
				}
			})
		}
	}
}
