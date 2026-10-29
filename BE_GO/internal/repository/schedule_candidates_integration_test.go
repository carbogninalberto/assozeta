//go:build schedulepageintegration

package repository_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	ownedpgx "github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/migrations"
	"github.com/jackc/pgx/v5"
	"github.com/jmoiron/sqlx"
)

type pageQuery struct {
	SQL  string
	Args []any
}
type pageQueries struct {
	mu     sync.Mutex
	values []pageQuery
}

func (q *pageQueries) TraceQueryStart(ctx context.Context, _ *pgx.Conn, data pgx.TraceQueryStartData) context.Context {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.values = append(q.values, pageQuery{data.SQL, append([]any(nil), data.Args...)})
	return ctx
}
func (*pageQueries) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}
func (q *pageQueries) reset()                                                        { q.mu.Lock(); defer q.mu.Unlock(); q.values = nil }
func (q *pageQueries) snapshot() []pageQuery {
	q.mu.Lock()
	defer q.mu.Unlock()
	return append([]pageQuery(nil), q.values...)
}

type afterCandidateRead struct {
	store domain.PeriodicCandidateStore
	after func()
}

func (r afterCandidateRead) HighWater(ctx context.Context) (int64, error) {
	return r.store.HighWater(ctx)
}

func (r afterCandidateRead) ListCandidates(ctx context.Context, id, through int64, limit int) ([]domain.PeriodicCandidate, error) {
	rows, err := r.store.ListCandidates(ctx, id, through, limit)
	if err == nil && r.after != nil {
		r.after()
	}
	return rows, err
}

type pagePublisherFunc func(context.Context, int64) (domain.OccurrenceResult, error)

func (f pagePublisherFunc) PublishNext(ctx context.Context, id int64) (domain.OccurrenceResult, error) {
	return f(ctx, id)
}

func TestSchedulePagesPostgres(t *testing.T) {
	dsn := os.Getenv("ASSOZETA_SCHEDULE_PAGE_DSN")
	if dsn == "" {
		t.Fatal("required private PostgreSQL and actual Beat migrations; no skipped proof")
	}
	cfg, err := pgx.ParseConfig(dsn)
	if err != nil || cfg.Database != "assozeta_contract_schedule_page" || cfg.User != "contracts" || !strings.HasPrefix(filepath.Base(cfg.Host), "az-schedule-page-") {
		t.Fatal("private schedule page fixture required")
	}
	queries := &pageQueries{}
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	cfg.Tracer = queries
	db := sqlx.NewDb(ownedpgx.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(8)
	db.SetMaxIdleConns(8)
	ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
	defer cancel()
	exec := func(sql string, args ...any) {
		t.Helper()
		if _, e := db.ExecContext(ctx, sql, args...); e != nil {
			t.Fatal(e)
		}
	}
	var actualHistory int
	if e := db.GetContext(ctx, &actualHistory, `SELECT count(*) FROM django_migrations WHERE app='django_celery_beat'`); e != nil || actualHistory == 0 {
		t.Fatal("real Beat fixture missing", e)
	}
	if e := migrations.Apply(dsn); e != nil {
		t.Fatal(e)
	}
	exec(`CREATE TABLE instance_datarestore(state text,completed_at timestamptz)`)
	candidates := repository.NewScheduleCandidates(db)
	lock := repository.NewRestoreFileLock(filepath.Join(t.TempDir(), "operations.lock"))
	publisher := service.NewOccurrencePublisher(repository.NewOccurrences(db), lock, repository.NewRestoreStates(db))
	seed := func(n int) {
		t.Helper()
		exec(`TRUNCATE assozeta_go_periodic_occurrences,assozeta_go_jobs; DELETE FROM django_celery_beat_periodictask; DELETE FROM django_celery_beat_intervalschedule; DELETE FROM instance_datarestore`)
		exec(`UPDATE assozeta_go_job_ownership SET runtime='go',generation=2`)
		exec(`INSERT INTO django_celery_beat_intervalschedule(id,every,period) VALUES(1,60,'seconds')`)
		exec(`INSERT INTO django_celery_beat_periodictask(id,name,task,args,kwargs,headers,enabled,last_run_at,total_run_count,date_changed,description,one_off,interval_id)
 SELECT g*3,'fixture-'||g,CASE WHEN g%17=0 THEN 'reset_communication_daily_email_balance' ELSE 'unrelated.historical.task' END,'[]','{}','{}',g%5<>0,clock_timestamp()-interval '10 minutes',0,clock_timestamp()-interval '1 hour','retained source description',false,1 FROM generate_series(1,$1::int) g`, n)
		exec(`INSERT INTO django_celery_beat_periodictasks(ident,last_update) VALUES(1,'2000-01-01Z') ON CONFLICT(ident) DO UPDATE SET last_update=EXCLUDED.last_update`)
		exec(`ANALYZE django_celery_beat_periodictask`)
	}
	pager := func(store domain.PeriodicCandidateStore, limit int) *service.SchedulePager {
		t.Helper()
		p, e := service.NewSchedulePager(store, publisher, service.SchedulePageOptions{Limit: limit, TickTimeout: 5 * time.Second, SourceTimeout: time.Second})
		if e != nil {
			t.Fatal(e)
		}
		return p
	}
	noPublication := func() {
		t.Helper()
		var count int
		if e := db.GetContext(ctx, &count, `SELECT (SELECT count(*) FROM assozeta_go_jobs)+(SELECT count(*) FROM assozeta_go_periodic_occurrences)+(SELECT coalesce(sum(total_run_count),0) FROM django_celery_beat_periodictask)`); e != nil || count != 0 {
			t.Fatal("unexpected durable mutation", count, e)
		}
	}

	t.Run("invalid seek bounds fail before database access", func(t *testing.T) {
		queries.reset()
		for _, in := range []struct {
			after int64
			limit int
		}{{-1, 16}, {math.MaxInt32 + 1, 16}, {0, 0}, {0, 65}, {0, -1}} {
			if _, e := candidates.ListCandidates(ctx, in.after, math.MaxInt32, in.limit); e == nil {
				t.Fatal("invalid bounds accepted", in)
			}
		}
		for _, in := range [][2]int64{{0, -1}, {5, 4}, {0, math.MaxInt32 + 1}} {
			if _, e := candidates.ListCandidates(ctx, in[0], in[1], 16); e == nil {
				t.Fatal("invalid rotation range accepted", in)
			}
		}
		if len(queries.snapshot()) != 0 {
			t.Fatal("invalid input reached database")
		}
	})

	t.Run("mixed sparse datasets use one bounded primary key seek with measured buffers", func(t *testing.T) {
		// Planner settings are untouched. Small tables may legitimately scan/sort;
		// the larger fixtures must demonstrate the bounded indexed range. Buffer
		// assertions apply to these measured datasets, not arbitrary table bloat.
		reports := []map[string]any{}
		for _, n := range []int{100, 1000, 10000} {
			seed(n)
			queries.reset()
			highest, e := candidates.HighWater(ctx)
			observed := queries.snapshot()
			if e != nil || highest != int64(n*3) || len(observed) != 1 {
				t.Fatal("highwater query bound", highest, e)
			}
			var highRaw string
			if e = db.GetContext(ctx, &highRaw, "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) "+observed[0].SQL); e != nil {
				t.Fatal(e)
			}
			var highPlan []map[string]any
			if e = json.Unmarshal([]byte(highRaw), &highPlan); e != nil {
				t.Fatal(e)
			}
			high := highPlan[0]["Plan"].(map[string]any)
			highEncoded := string(mustPageJSON(t, high))
			if (n >= 1000 && (!strings.Contains(highEncoded, "django_celery_beat_periodictask_pkey") || strings.Contains(highEncoded, `"Node Type":"Sort"`))) || high["Actual Rows"].(float64) != 1 {
				t.Fatal("highwater lost bounded backward PK lookup", highEncoded)
			}
			if high["Shared Hit Blocks"].(float64)+high["Shared Read Blocks"].(float64) > 8 {
				t.Fatal("highwater buffer bound", high)
			}
			reports = append(reports, map[string]any{"rows": n, "kind": "highwater", "plan": high})
			for _, limit := range []int{1, 16, 64} {
				after := int64(n / 2 * 3)
				queries.reset()
				rows, e := candidates.ListCandidates(ctx, after, int64(n*3), limit)
				captured := queries.snapshot()
				if e != nil || len(rows) > limit || len(captured) != 1 {
					t.Fatal("candidate query bound", n, limit, len(rows), len(captured), e)
				}
				if len(rows) != min(limit, n/2) {
					t.Fatal("filtered or incomplete raw page", n, limit, len(rows))
				}
				for i, row := range rows {
					if row.ID != after+int64((i+1)*3) {
						t.Fatal("seek skipped a raw row", row.ID)
					}
				}
				sql := strings.ToLower(captured[0].SQL)
				for _, forbidden := range []string{" offset ", "count(", "select *", "last_run_at", "args", "enabled=", "task="} {
					if strings.Contains(sql, forbidden) {
						t.Fatal("unbounded or overfetching query", sql)
					}
				}
				var raw string
				if e = db.GetContext(ctx, &raw, "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) "+captured[0].SQL, captured[0].Args...); e != nil {
					t.Fatal(e)
				}
				var parsed []map[string]any
				if e = json.Unmarshal([]byte(raw), &parsed); e != nil {
					t.Fatal(e)
				}
				plan := parsed[0]["Plan"].(map[string]any)
				if plan["Actual Rows"].(float64) > float64(limit) {
					t.Fatal("plan overfetched", plan)
				}
				encoded := string(mustPageJSON(t, plan))
				if n >= 1000 && (!strings.Contains(encoded, "django_celery_beat_periodictask_pkey") || strings.Contains(encoded, `"Node Type":"Seq Scan"`) || strings.Contains(encoded, `"Node Type":"Sort"`)) {
					t.Fatal("candidate query lost PK seek", n, limit, encoded)
				}
				buffers := plan["Shared Hit Blocks"].(float64) + plan["Shared Read Blocks"].(float64)
				if buffers > float64(limit+16) {
					t.Fatal("candidate buffer bound", n, limit, buffers)
				}
				reports = append(reports, map[string]any{"rows": n, "limit": limit, "after_id": after, "plan": plan})
			}
		}
		path := os.Getenv("ASSOZETA_SCHEDULE_PAGE_PLAN_OUTPUT")
		if path == "" {
			t.Fatal("required plan receipt path missing")
		}
		if e := os.WriteFile(path, mustPageJSON(t, reports), 0600); e != nil {
			t.Fatal(e)
		}
	})

	t.Run("full rotation visits every sparse source despite disabled and unrelated prefixes", func(t *testing.T) {
		seed(1000)
		exec(`UPDATE django_celery_beat_periodictask SET task='unrelated.historical.task'`)
		p := pager(candidates, 0)
		seen := map[int64]bool{}
		var cursor domain.ScheduleCursor
		for tick := 0; tick < 65; tick++ {
			queries.reset()
			result, e := p.Tick(ctx, cursor)
			if e != nil || len(result.Outcomes) > 16 || len(queries.snapshot()) != 1+boolInt(tick == 0) {
				t.Fatal("rotation bound", e, len(result.Outcomes), len(queries.snapshot()))
			}
			for _, outcome := range result.Outcomes {
				if seen[outcome.SourceID] {
					t.Fatal("duplicate before wrap")
				}
				seen[outcome.SourceID] = true
			}
			cursor = result.NextCursor
			if result.EndOfRange {
				if cursor != (domain.ScheduleCursor{}) || len(seen) != 1000 {
					t.Fatal("unfair rotation", cursor, len(seen))
				}
				break
			}
		}
		if len(seen) != 1000 {
			t.Fatal("later rows starved", len(seen))
		}
		noPublication()
	})

	t.Run("higher ID arrivals cannot prevent a finite rotation and low ID revisit", func(t *testing.T) {
		seed(10)
		exec(`UPDATE django_celery_beat_periodictask SET task='unrelated.historical.task'`)
		p := pager(candidates, 3)
		var cursor domain.ScheduleCursor
		seen := map[int64]bool{}
		for tick := 0; tick < 4; tick++ {
			result, e := p.Tick(ctx, cursor)
			if e != nil {
				t.Fatal(e)
			}
			for _, outcome := range result.Outcomes {
				if outcome.SourceID > 30 || seen[outcome.SourceID] {
					t.Fatal("arrival escaped rotation watermark", outcome)
				}
				seen[outcome.SourceID] = true
			}
			cursor = result.NextCursor
			if tick < 3 && (cursor.ThroughID != 30 || result.EndOfRange) {
				t.Fatal("watermark changed within rotation", result)
			}
			exec(`INSERT INTO django_celery_beat_periodictask(id,name,task,args,kwargs,headers,enabled,last_run_at,total_run_count,date_changed,description,one_off,interval_id)
 SELECT 300+$1::int*30+g*3,'arrival-'||$1::int||'-'||g,'unrelated.arrival','[]','{}','{}',true,clock_timestamp(),0,clock_timestamp(),'',false,1 FROM generate_series(1,10) g`, tick)
		}
		if cursor != (domain.ScheduleCursor{}) || len(seen) != 10 {
			t.Fatal("new arrivals starved wrap", cursor, len(seen))
		}
		next, e := p.Tick(ctx, cursor)
		if e != nil || len(next.Outcomes) != 3 || next.Outcomes[0].SourceID != 3 || next.NextCursor.ThroughID <= 30 {
			t.Fatal("low IDs not revisited with next watermark", next, e)
		}
		noPublication()
	})

	t.Run("unsupported busy and deleted sources cannot starve a later valid page", func(t *testing.T) {
		seed(4)
		exec(`UPDATE django_celery_beat_periodictask SET task='reset_communication_daily_email_balance',enabled=true; UPDATE django_celery_beat_periodictask SET queue='unsupported' WHERE id=3`)
		held, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer held.Rollback()
		if _, e = held.ExecContext(ctx, `SELECT id FROM django_celery_beat_periodictask WHERE id=6 FOR UPDATE`); e != nil {
			t.Fatal(e)
		}
		store := afterCandidateRead{store: candidates, after: func() { exec(`DELETE FROM django_celery_beat_periodictask WHERE id=9`) }}
		first, e := pager(store, 3).Tick(ctx, domain.ScheduleCursor{})
		if e != nil || len(first.Outcomes) != 3 || string(first.Outcomes[0].Status) != "unsupported" || string(first.Outcomes[1].Status) != "busy" || string(first.Outcomes[2].Status) != "not_found" || first.NextCursor != (domain.ScheduleCursor{AfterID: 9, ThroughID: 12}) {
			t.Fatal("bounded row failures stopped progression", first, e)
		}
		_ = held.Rollback()
		last, e := pager(candidates, 3).Tick(ctx, first.NextCursor)
		if e != nil || len(last.Outcomes) != 1 || string(last.Outcomes[0].Status) != "published" || !last.EndOfRange || last.NextCursor != (domain.ScheduleCursor{}) {
			t.Fatal("later source starved", last, e)
		}
	})

	t.Run("markerless edits are reloaded after rotation and after candidate selection", func(t *testing.T) {
		seed(1)
		p := pager(candidates, 16)
		first, e := p.Tick(ctx, domain.ScheduleCursor{})
		if e != nil || len(first.Outcomes) != 1 || string(first.Outcomes[0].Status) != "unrelated" {
			t.Fatal(first, e)
		}
		exec(`UPDATE django_celery_beat_periodictask SET task='reset_communication_daily_email_balance',enabled=true`)
		edited := afterCandidateRead{store: candidates, after: func() { exec(`UPDATE django_celery_beat_periodictask SET kwargs='{"edited":true}'`) }}
		second, e := pager(edited, 16).Tick(ctx, first.NextCursor)
		if e != nil || string(second.Outcomes[0].Status) != "unsupported" {
			t.Fatal("cached definition admitted edit", second, e)
		}
		noPublication()
		exec(`UPDATE django_celery_beat_periodictask SET kwargs='{}'`)
		third, e := p.Tick(ctx, second.NextCursor)
		if e != nil || string(third.Outcomes[0].Status) != "published" {
			t.Fatal("markerless correction not seen", third, e)
		}
		var marker string
		if e = db.GetContext(ctx, &marker, `SELECT last_update::text FROM django_celery_beat_periodictasks WHERE ident=1`); e != nil || !strings.HasPrefix(marker, "2000-01-01") {
			t.Fatal("marker unexpectedly rewritten", marker, e)
		}
	})

	t.Run("publication has a fixed per attempt query budget without history scan", func(t *testing.T) {
		seed(16)
		exec(`UPDATE django_celery_beat_periodictask SET task='reset_communication_daily_email_balance',enabled=true`)
		queries.reset()
		result, e := pager(candidates, 16).Tick(ctx, domain.ScheduleCursor{})
		captured := queries.snapshot()
		// Two discovery reads, eleven existing statements and one acknowledged
		// shared server fence for each successful publication; count every statement.
		if e != nil || len(result.Outcomes) != 16 || len(captured) != 2+(11+1)*16 {
			t.Fatal("publication query budget", len(captured), e)
		}
		candidateReads := 0
		for _, q := range captured {
			text := strings.ToLower(q.SQL)
			if strings.Contains(text, "order by id") && strings.Contains(text, "limit") {
				candidateReads++
			}
			if strings.Contains(text, "completed_at") {
				t.Fatal("irrelevant completion history read")
			}
		}
		if candidateReads != 2 {
			t.Fatal("candidate query count", candidateReads)
		}
		for _, outcome := range result.Outcomes {
			if string(outcome.Status) != "published" {
				t.Fatal(result)
			}
		}
		t.Logf("16 publications: %d statements including highwater and candidate reads", len(captured))
	})

	t.Run("Django ownership and restore admission stop the page without durable effects", func(t *testing.T) {
		for _, mode := range []string{"django", "maintenance", "pending"} {
			seed(2)
			exec(`UPDATE django_celery_beat_periodictask SET task='reset_communication_daily_email_balance',enabled=true`)
			var release func()
			switch mode {
			case "django":
				exec(`UPDATE assozeta_go_job_ownership SET runtime='django'`)
			case "maintenance":
				held, e := lock.Acquire(ctx, true, true)
				if e != nil {
					t.Fatal(e)
				}
				release = func() { _ = held.Close() }
			case "pending":
				exec(`INSERT INTO instance_datarestore(state) VALUES('running')`)
			}
			result, e := pager(candidates, 16).Tick(ctx, domain.ScheduleCursor{})
			if release != nil {
				release()
			}
			if e == nil || len(result.Outcomes) != 1 || result.NextCursor != (domain.ScheduleCursor{AfterID: 3, ThroughID: 6}) {
				t.Fatal("admission did not stop bounded page", mode, result, e)
			}
			noPublication()
		}
		// Ownership is checked per publication, not only at page admission.
		seed(2)
		exec(`UPDATE django_celery_beat_periodictask SET task='reset_communication_daily_email_balance',enabled=true`)
		changing := pagePublisherFunc(func(ctx context.Context, id int64) (domain.OccurrenceResult, error) {
			result, e := publisher.PublishNext(ctx, id)
			if e == nil && result.Published {
				exec(`UPDATE assozeta_go_job_ownership SET runtime='django',generation=generation+1`)
			}
			return result, e
		})
		p, e := service.NewSchedulePager(candidates, changing, service.SchedulePageOptions{Limit: 16})
		if e != nil {
			t.Fatal(e)
		}
		result, e := p.Tick(ctx, domain.ScheduleCursor{})
		if !errors.Is(e, domain.ErrOccurrenceInactive) || len(result.Outcomes) != 2 || string(result.Outcomes[0].Status) != "published" || string(result.Outcomes[1].Status) != "inactive" {
			t.Fatal("page reused stale owner", result, e)
		}
		var count int
		if e = db.GetContext(ctx, &count, `SELECT count(*) FROM assozeta_go_periodic_occurrences`); e != nil || count != 1 {
			t.Fatal("owner loss published later source", count, e)
		}
	})

	t.Run("SQL failure and cancelled source roll back and stop later publication", func(t *testing.T) {
		seed(2)
		exec(`UPDATE django_celery_beat_periodictask SET task='reset_communication_daily_email_balance',enabled=true`)
		exec(`CREATE FUNCTION page_reject_cursor() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'fixture'; END $$; CREATE TRIGGER page_reject_cursor BEFORE UPDATE ON django_celery_beat_periodictask FOR EACH ROW EXECUTE FUNCTION page_reject_cursor()`)
		result, e := pager(candidates, 16).Tick(ctx, domain.ScheduleCursor{})
		exec(`DROP TRIGGER page_reject_cursor ON django_celery_beat_periodictask; DROP FUNCTION page_reject_cursor()`)
		if e == nil || len(result.Outcomes) != 1 || string(result.Outcomes[0].Status) != "failed" {
			t.Fatal("SQL failure concealed", result, e)
		}
		noPublication()
		cancelled, stop := context.WithCancel(ctx)
		stop()
		_, e = pager(candidates, 16).Tick(cancelled, domain.ScheduleCursor{})
		if !errors.Is(e, context.Canceled) {
			t.Fatal("cancelled tick continued", e)
		}
		noPublication()
		// A source timeout must cancel an actual blocked owner-fence query,
		// roll back its transaction and leave later sources untouched.
		held, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer held.Rollback()
		if _, e = held.ExecContext(ctx, `SELECT singleton FROM assozeta_go_job_ownership FOR UPDATE`); e != nil {
			t.Fatal(e)
		}
		bounded, e := service.NewSchedulePager(candidates, publisher, service.SchedulePageOptions{Limit: 16, TickTimeout: 2 * time.Second, SourceTimeout: 50 * time.Millisecond})
		if e != nil {
			t.Fatal(e)
		}
		result, e = bounded.Tick(ctx, domain.ScheduleCursor{})
		_ = held.Rollback()
		if !errors.Is(e, context.DeadlineExceeded) || len(result.Outcomes) != 1 || result.Outcomes[0].Status != domain.SchedulePageCancelled || result.NextCursor != (domain.ScheduleCursor{AfterID: 3, ThroughID: 6}) {
			t.Fatal("blocked source ignored cancellation or lost its rotation cursor", result, e)
		}
		noPublication()
	})
}

func mustPageJSON(t *testing.T, value any) []byte {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatal(fmt.Sprint(err))
	}
	return raw
}

func boolInt(value bool) int {
	if value {
		return 1
	}
	return 0
}
