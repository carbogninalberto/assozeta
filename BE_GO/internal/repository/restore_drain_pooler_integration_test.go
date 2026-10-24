//go:build restoredrainpoolerintegration

package repository

import (
	"context"
	"errors"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

const poolerSharedFenceSQL = `SELECT pg_advisory_xact_lock_shared(1095981639,1381192786)`
const poolerExclusiveFenceSQL = `SELECT pg_advisory_xact_lock(1095981639,1381192786)`

func poolerMust(e error) {
	if e != nil {
		panic(e)
	}
}
func poolerWait(ctx context.Context, predicate func() bool) {
	for !predicate() {
		select {
		case <-ctx.Done():
			panic("fixture condition timeout")
		case <-time.After(time.Millisecond):
		}
	}
}

type poolerTrace struct {
	mu      sync.Mutex
	queries []string
	updates int
}

func (t *poolerTrace) TraceQueryStart(ctx context.Context, c *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.queries = append(t.queries, d.SQL)
	if len(d.SQL) >= 6 && d.SQL[:6] == "UPDATE" {
		t.updates++
	}
	return ctx
}
func (t *poolerTrace) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}
func (t *poolerTrace) assertFenceFirst() {
	t.mu.Lock()
	defer t.mu.Unlock()
	for i, q := range t.queries {
		if q == "begin" {
			if i+1 >= len(t.queries) || !strings.HasPrefix(t.queries[i+1], "SELECT pg_advisory_xact_lock_shared(") {
				panic(fmt.Sprint("shared fence not first statement ", t.queries))
			}
			return
		}
	}
	panic("no begin traced")
}
func poolerOpenRuntime(cfg *pgx.ConnConfig, failCancel bool, t *poolerTrace) *sqlx.DB {
	c := cfg.Copy()
	c.Tracer = t
	if failCancel {
		dial := c.DialFunc
		var calls atomic.Int32
		c.DialFunc = func(ctx context.Context, n, a string) (net.Conn, error) {
			if calls.Add(1) > 1 {
				return nil, errors.New("fixture cancel transport unavailable")
			}
			return dial(ctx, n, a)
		}
	}
	db := sqlx.NewDb(stdlib.OpenDB(*c), "pgx")
	db.SetMaxOpenConns(1)
	return db
}
func poolerScalar(ctx context.Context, db *sqlx.DB, q string, args ...any) int {
	var value int
	poolerMust(db.GetContext(ctx, &value, q, args...))
	return value
}

func TestRestoreDrainPooler(t *testing.T) {
	ctx, stop := context.WithTimeout(context.Background(), 60*time.Second)
	defer stop()
	cfg, e := pgx.ParseConfig(os.Getenv("ASSOZETA_POOLER_POOL_DSN"))
	poolerMust(e)
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	adminCfg, e := pgx.ParseConfig(os.Getenv("ASSOZETA_POOLER_ADMIN_DSN"))
	poolerMust(e)
	adminCfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	if cfg.Database != "assozeta_contract_pooler" || adminCfg.Database != cfg.Database || cfg.User != "contracts" || adminCfg.User != "contracts" || cfg.Host != "127.0.0.1" || adminCfg.Host != cfg.Host {
		t.Fatal("required private pooler fixture")
	}
	if cfg.Port == adminCfg.Port {
		panic("runtime must use distinct PgBouncer port")
	}
	admin := sqlx.NewDb(stdlib.OpenDB(*adminCfg), "pgx")
	defer admin.Close()
	fmt.Printf("RUNTIME pool_port=%d direct_admin_port=%d mode=transaction shared_key=(1095981639,1381192786)\n", cfg.Port, adminCfg.Port)
	a := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	defer a.Close()
	b := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	defer b.Close()
	ta, e := a.BeginTxx(ctx, nil)
	poolerMust(e)
	var first int
	poolerMust(ta.GetContext(ctx, &first, `SELECT pg_backend_pid()`))
	poolerMust(ta.Commit())
	tb, e := b.BeginTxx(ctx, nil)
	poolerMust(e)
	var borrowed int
	poolerMust(tb.GetContext(ctx, &borrowed, `SELECT pg_backend_pid()`))
	ta, e = a.BeginTxx(ctx, nil)
	poolerMust(e)
	var reassigned int
	poolerMust(ta.GetContext(ctx, &reassigned, `SELECT pg_backend_pid()`))
	poolerMust(ta.Commit())
	poolerMust(tb.Commit())
	if first != borrowed || first == reassigned {
		panic(fmt.Sprintf("no actual server reassignment: %d %d %d", first, borrowed, reassigned))
	}
	fmt.Printf("PASS transaction pool reassigns server: first=%d borrowed=%d next=%d\n", first, borrowed, reassigned)
	_, e = admin.ExecContext(ctx, `CREATE TABLE pooler_probe(id integer PRIMARY KEY,value integer NOT NULL); INSERT INTO pooler_probe VALUES(1,0); CREATE FUNCTION hold_pooler_commit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_advisory_xact_lock(7220001,1); RETURN NEW; END $$; CREATE CONSTRAINT TRIGGER deferred_gate AFTER UPDATE ON pooler_probe DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION hold_pooler_commit()`)
	poolerMust(e)
	control, e := admin.BeginTxx(ctx, nil)
	poolerMust(e)
	defer control.Rollback()
	_, e = control.ExecContext(ctx, `SELECT pg_advisory_xact_lock(7220001,1)`)
	poolerMust(e)
	wt := &poolerTrace{}
	writer := poolerOpenRuntime(cfg, true, wt)
	defer writer.Close()
	work, cancel := context.WithCancel(ctx)
	defer cancel()
	owned, e := beginOwnedTransaction(work, writer, nil)
	poolerMust(e)
	defer owned.Rollback()
	var writerPID int
	poolerMust(owned.tx.GetContext(work, &writerPID, `SELECT pg_backend_pid()`))
	_, e = owned.tx.ExecContext(work, `UPDATE pooler_probe SET value=9 WHERE id=1`)
	poolerMust(e)
	wt.assertFenceFirst()
	lock, e := os.CreateTemp("", "pooler-advisory-lock-")
	poolerMust(e)
	defer os.Remove(lock.Name())
	defer lock.Close()
	poolerMust(syscall.Flock(int(lock.Fd()), syscall.LOCK_SH))
	committed := make(chan error, 1)
	go func() { err := owned.Commit(); _ = syscall.Flock(int(lock.Fd()), syscall.LOCK_UN); committed <- err }()
	poolerWait(ctx, func() bool {
		return poolerScalar(ctx, admin, `SELECT count(*) FROM pg_stat_activity WHERE pid=$1 AND query='commit' AND wait_event='advisory'`, writerPID) == 1
	})
	cancel()
	select {
	case e = <-committed:
		if e == nil {
			panic("cancelled commit unexpectedly acknowledged")
		}
	case <-ctx.Done():
		panic("caller did not complete")
	}
	select {
	case <-owned.cleanup:
	default:
		panic("local helper cleanup absent")
	}
	restoring, e := os.OpenFile(lock.Name(), os.O_RDWR, 0600)
	poolerMust(e)
	defer restoring.Close()
	poolerMust(syscall.Flock(int(restoring.Fd()), syscall.LOCK_EX|syscall.LOCK_NB))
	timeoutDrain, e := a.BeginTxx(ctx, nil)
	poolerMust(e)
	_, e = timeoutDrain.ExecContext(ctx, `SET LOCAL lock_timeout = '100ms'`)
	poolerMust(e)
	_, e = timeoutDrain.ExecContext(ctx, poolerExclusiveFenceSQL)
	var pgerr *pgconn.PgError
	if !errors.As(e, &pgerr) || pgerr.Code != "55P03" {
		panic(fmt.Sprint("expected bounded exclusive drain failure ", e))
	}
	poolerMust(timeoutDrain.Rollback())
	fmt.Println("PASS bounded pooled exclusive drain rejected busy writer before any export read")
	drainDone := make(chan error, 1)
	drainStarted := make(chan int, 1)
	go func() {
		tx, err := a.BeginTxx(ctx, nil)
		if err != nil {
			drainDone <- err
			return
		}
		defer tx.Rollback()
		var pid int
		err = tx.GetContext(ctx, &pid, `SELECT pg_backend_pid()`)
		if err != nil {
			drainDone <- err
			return
		}
		drainStarted <- pid
		_, err = tx.ExecContext(ctx, poolerExclusiveFenceSQL)
		if err == nil {
			err = tx.Commit()
		}
		drainDone <- err
	}()
	drainPID := <-drainStarted
	poolerWait(ctx, func() bool {
		return poolerScalar(ctx, admin, `SELECT count(*) FROM pg_stat_activity WHERE pid=$1 AND wait_event='advisory'`, drainPID) == 1 || poolerScalar(ctx, admin, `SELECT count(*) FROM pg_locks WHERE pid=$1 AND locktype='advisory' AND classid=1095981639 AND objid=1381192786`, writerPID) == 0
	})
	writerStillHolds := poolerScalar(ctx, admin, `SELECT count(*) FROM pg_locks WHERE pid=$1 AND locktype='advisory' AND classid=1095981639 AND objid=1381192786`, writerPID) > 0
	if writerStillHolds {
		select {
		case e := <-drainDone:
			panic(fmt.Sprint("drain crossed live writer ", e))
		default:
		}
	}
	poolerMust(control.Rollback())
	select {
	case e = <-drainDone:
		poolerMust(e)
	case <-ctx.Done():
		panic("drain did not finish")
	}
	before := poolerScalar(ctx, admin, `SELECT value FROM pooler_probe WHERE id=1`)
	time.Sleep(20 * time.Millisecond)
	after := poolerScalar(ctx, admin, `SELECT value FROM pooler_probe WHERE id=1`)
	if before != after {
		panic("effect arrived after drain")
	}
	fmt.Printf("PASS cancelled COMMIT/cancel transport unavailable: local cleanup then pooled drain; writer_still_held=%t snapshot=%d final=%d\n", writerStillHolds, before, after)
	poolerMust(syscall.Flock(int(restoring.Fd()), syscall.LOCK_UN))

	hold, e := a.BeginTxx(ctx, nil)
	poolerMust(e)
	defer hold.Rollback()
	_, e = hold.ExecContext(ctx, poolerExclusiveFenceSQL)
	poolerMust(e)
	waiterTrace := &poolerTrace{}
	waiter := poolerOpenRuntime(cfg, true, waiterTrace)
	defer waiter.Close()
	waiting, stopWait := context.WithCancel(ctx)
	defer stopWait()
	attempt := make(chan error, 1)
	go func() {
		tx, err := beginOwnedTransaction(waiting, waiter, nil)
		if err == nil {
			_, err = tx.tx.ExecContext(waiting, `UPDATE pooler_probe SET value=88 WHERE id=1`)
			_ = tx.Rollback()
		}
		attempt <- err
	}()
	poolerWait(ctx, func() bool {
		return poolerScalar(ctx, admin, `SELECT count(*) FROM pg_stat_activity WHERE query LIKE 'SELECT pg_advisory_xact_lock_shared(%' AND wait_event='advisory'`) > 0
	})
	stopWait()
	select {
	case e = <-attempt:
		if e == nil {
			panic("cancelled shared wait accepted")
		}
	case <-ctx.Done():
		panic("cancelled waiter stuck")
	}
	poolerMust(hold.Rollback())
	time.Sleep(20 * time.Millisecond)
	waiterTrace.assertFenceFirst()
	waiterTrace.mu.Lock()
	updates := waiterTrace.updates
	waiterTrace.mu.Unlock()
	if updates != 0 || poolerScalar(ctx, admin, `SELECT value FROM pooler_probe WHERE id=1`) != after {
		panic("cancelled shared waiter emitted mutation")
	}
	fmt.Println("PASS cancelled first-shared-lock waiter emitted zero business UPDATE commands")

	// Run the actual current shell drain SQL, adapting only Compose transport to
	// this disposable container. The helper is a real flock implementation for
	// hosts whose base utilities do not include the Linux flock executable.
	_, e = admin.ExecContext(ctx, `DROP TRIGGER deferred_gate ON pooler_probe; UPDATE pooler_probe SET value=0 WHERE id=1; CREATE CONSTRAINT TRIGGER deferred_gate AFTER UPDATE ON pooler_probe DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION hold_pooler_commit()`)
	poolerMust(e)
	cliControl, e := admin.BeginTxx(ctx, nil)
	poolerMust(e)
	defer cliControl.Rollback()
	_, e = cliControl.ExecContext(ctx, `SELECT pg_advisory_xact_lock(7220001,1)`)
	poolerMust(e)
	cliTrace := &poolerTrace{}
	cliWriter := poolerOpenRuntime(cfg, true, cliTrace)
	defer cliWriter.Close()
	cliWork, stopCLIWriter := context.WithCancel(ctx)
	defer stopCLIWriter()
	cliOwned, e := beginOwnedTransaction(cliWork, cliWriter, nil)
	poolerMust(e)
	defer cliOwned.Rollback()
	var cliPID int
	poolerMust(cliOwned.tx.GetContext(cliWork, &cliPID, `SELECT pg_backend_pid()`))
	_, e = cliOwned.tx.ExecContext(cliWork, `UPDATE pooler_probe SET value=19 WHERE id=1`)
	poolerMust(e)
	cliTrace.assertFenceFirst()
	poolerMust(syscall.Flock(int(lock.Fd()), syscall.LOCK_SH))
	cliCommit := make(chan error, 1)
	go func() { err := cliOwned.Commit(); _ = syscall.Flock(int(lock.Fd()), syscall.LOCK_UN); cliCommit <- err }()
	poolerWait(ctx, func() bool {
		return poolerScalar(ctx, admin, `SELECT count(*) FROM pg_stat_activity WHERE pid=$1 AND query='commit' AND wait_event='advisory'`, cliPID) == 1
	})
	stopCLIWriter()
	select {
	case e = <-cliCommit:
		if e == nil {
			panic("CLI writer expected cancellation")
		}
	case <-ctx.Done():
		panic("CLI writer cleanup timeout")
	}
	select {
	case <-cliOwned.cleanup:
	default:
		panic("CLI writer local cleanup absent")
	}
	definitions, e := os.ReadFile(os.Getenv("ASSOZETA_POOLER_CLI_SOURCE"))
	poolerMust(e)
	split := strings.SplitN(string(definitions), "\ncommand=${1:-help}\n", 2)
	if len(split) != 2 {
		panic("production CLI definitions boundary changed")
	}
	fixtureDir, e := os.MkdirTemp("", "pooler-cli-drain-")
	poolerMust(e)
	defer os.RemoveAll(fixtureDir)
	poolerMust(os.WriteFile(filepath.Join(fixtureDir, ".env"), []byte("DBUSER=contracts\nDBNAME="+cfg.Database+"\n"), 0600))
	shim := filepath.Join(fixtureDir, "flock.py")
	poolerMust(os.WriteFile(shim, []byte("import fcntl,sys\nfcntl.flock(int(sys.argv[1]),fcntl.LOCK_EX)\n"), 0600))
	marker := filepath.Join(fixtureDir, "snapshot")
	script := split[0] + `
prod_compose() {
    if [ "$*" = '--profile tools config --services' ]; then printf 'migrate-go\n'; return 0; fi
    [ "$1" = exec ] && [ "$2" = -T ] && [ "$3" = postgres ] || return 91
    shift 3
    docker exec "$ASSOZETA_POOLER_CONTAINER" "$@"
}
exec 8>"$ASSOZETA_POOLER_LOCK"
"$ASSOZETA_POOLER_PYTHON" "$ASSOZETA_POOLER_FLOCK" 8
prod_operations_lock_held=true
drain_prod_go_transactions
prod_compose exec -T postgres psql -X -v ON_ERROR_STOP=1 -U contracts -d "$ASSOZETA_POOLER_DATABASE" -Atc 'SELECT value FROM pooler_probe WHERE id=1' > "$ASSOZETA_POOLER_MARKER"
`
	shell := exec.CommandContext(ctx, "sh", "-c", script)
	shell.Env = append(os.Environ(), "ASSOZETA_INSTALL_ROOT="+fixtureDir, "ASSOZETA_POOLER_LOCK="+lock.Name(), "ASSOZETA_POOLER_FLOCK="+shim, "ASSOZETA_POOLER_MARKER="+marker, "ASSOZETA_POOLER_DATABASE="+cfg.Database)
	var shellLog strings.Builder
	shell.Stdout = &shellLog
	shell.Stderr = &shellLog
	poolerMust(shell.Start())
	shellDone := make(chan error, 1)
	go func() { shellDone <- shell.Wait() }()
	shellFinished := false
	defer func() {
		if !shellFinished {
			_ = shell.Process.Kill()
			<-shellDone
		}
	}()
	poolerWait(ctx, func() bool {
		return poolerScalar(ctx, admin, `SELECT count(*) FROM pg_stat_activity WHERE query LIKE 'BEGIN; SET LOCAL statement_timeout%' AND wait_event='advisory'`) > 0
	})
	if _, err := os.Stat(marker); !os.IsNotExist(err) {
		panic("CLI snapshot marker appeared before server drain")
	}
	held, e := os.OpenFile(lock.Name(), os.O_RDWR, 0600)
	poolerMust(e)
	defer held.Close()
	e = syscall.Flock(int(held.Fd()), syscall.LOCK_SH|syscall.LOCK_NB)
	if e == nil {
		_ = syscall.Flock(int(held.Fd()), syscall.LOCK_UN)
		panic("CLI did not retain actual exclusive file lease")
	}
	poolerMust(cliControl.Rollback())
	select {
	case e = <-shellDone:
		shellFinished = true
		if e != nil {
			panic(fmt.Sprint("actual CLI drain failed: ", e, " ", shellLog.String()))
		}
	case <-ctx.Done():
		panic("actual CLI drain timeout")
	}
	snapshot, e := os.ReadFile(marker)
	poolerMust(e)
	if strings.TrimSpace(string(snapshot)) != "19" {
		panic(fmt.Sprint("CLI snapshot omitted late commit ", string(snapshot)))
	}
	fmt.Println("PASS actual CLI drain retains exclusive file lease and delays snapshot until remote commit")
}
