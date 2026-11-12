//go:build restoreintegration

package service

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

type restoreQueries struct{ count atomic.Int64 }

func (q *restoreQueries) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	if strings.HasPrefix(strings.ToUpper(strings.TrimSpace(d.SQL)), "SELECT") {
		q.count.Add(1)
	}
	return ctx
}
func (*restoreQueries) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

func TestRestoreAdmissionPostgres(t *testing.T) {
	if os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN") == "" {
		t.Fatal("run accounts.py --only restore-admission")
	}
	cfg, err := pgx.ParseConfig(os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN"))
	if err != nil {
		t.Fatal("invalid private database configuration")
	}
	queries := &restoreQueries{}
	cfg.Tracer = queries
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	db := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(4)
	db.SetMaxIdleConns(4)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	const first = "00000000-0000-4000-8000-000000085000"
	const end = "00000000-0000-4000-8000-000000085512"
	var collisions int
	if err := db.GetContext(ctx, &collisions, `SELECT count(*) FROM instance_datarestore WHERE id >= $1 AND id < $2`, first, end); err != nil || collisions != 0 {
		t.Fatal("restore fixture IDs collide")
	}
	cleanup := func() {
		if _, err := db.ExecContext(context.Background(), `DELETE FROM instance_datarestore WHERE id >= $1 AND id < $2`, first, end); err != nil {
			t.Error("restore fixture cleanup failed")
		}
	}
	defer cleanup()
	states := repository.NewRestoreStates(db)
	check := func(want bool) {
		t.Helper()
		queries.count.Store(0)
		actual, err := states.Pending(ctx)
		if err != nil || actual != want || queries.count.Load() != 1 {
			t.Fatal("global pending check changed value or exceeded one SELECT")
		}
	}
	for _, size := range []int{1, 16, 128, 512} {
		cleanup()
		_, err := db.ExecContext(ctx, `INSERT INTO instance_datarestore
		 (id,owner_id,association_id,state,stage,completed_at,created_at,updated_at,upload_path,sha256,version,preview,backup_path,backup_sha256,media,error,allow_missing_media,attempts)
		 SELECT ('00000000-0000-4000-8000-' || lpad((85000+n)::text,12,'0'))::uuid,
		 owner_id,('00000000-0000-4000-8000-' || lpad((85000+n)::text,12,'0'))::uuid,
		 state,stage,completed_at,created_at,updated_at,upload_path,sha256,version,preview,backup_path,backup_sha256,media,error,allow_missing_media,attempts
		 FROM instance_datarestore CROSS JOIN generate_series(0,$1::integer-1) n WHERE id=$2`, size, "00000000-0000-4000-8000-000000084000")
		if err != nil {
			t.Fatal("cannot create restore state fixtures")
		}
		check(false)
		for _, state := range []string{"queued", "running", "completed", "failed", "review"} {
			if _, err := db.ExecContext(ctx, `UPDATE instance_datarestore SET state=$1 WHERE id=$2`, state, first); err != nil {
				t.Fatal(err)
			}
			check(state == "queued" || state == "running")
		}
	}
	lock := repository.NewRestoreFileLock(filepath.Join(t.TempDir(), "lifecycle.lock"))
	gate := NewRestoreAdmission(lock, states, true)
	queries.count.Store(0)
	for _, method := range []string{"GET", "HEAD", "POST"} {
		lease, err := gate.AcquireHTTP(ctx, method, "/two-fa/info")
		if err != nil {
			t.Fatal(err)
		}
		if err := lease.Close(); err != nil {
			t.Fatal(err)
		}
	}
	if queries.count.Load() != 1 {
		t.Fatal("GET/HEAD admission unexpectedly queried restore state")
	}
	queries.count.Store(0)
	var group sync.WaitGroup
	for i := 0; i < 32; i++ {
		group.Add(1)
		go func() {
			defer group.Done()
			lease, err := gate.AcquireHTTP(ctx, "POST", "/two-fa/info")
			if err != nil {
				t.Error(err)
				return
			}
			if err := lease.Close(); err != nil {
				t.Error(err)
			}
		}()
	}
	group.Wait()
	if queries.count.Load() != 32 || db.Stats().OpenConnections > 4 {
		t.Fatal("concurrent restore admission exceeded query/pool bounds")
	}
	var plan json.RawMessage
	if err := db.GetContext(ctx, &plan, `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT EXISTS (SELECT 1 FROM instance_datarestore WHERE state IN ($1,$2))`, "queued", "running"); err != nil {
		t.Fatal(err)
	}
	if path := os.Getenv("ASSOZETA_RESTORE_PLAN_OUTPUT"); path == "" || os.WriteFile(path, plan, 0600) != nil {
		t.Fatal("cannot retain restore query plan")
	}
	canceled, cancelNow := context.WithCancel(ctx)
	cancelNow()
	if _, err := states.Pending(canceled); !errors.Is(err, context.Canceled) {
		t.Fatal("pending query ignored cancellation")
	}
	cleanup()
	// A separate closed pool proves a database failure releases its file lease.
	closed := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	_ = closed.Close()
	lease, err := NewRestoreAdmission(lock, repository.NewRestoreStates(closed), true).AcquireHTTP(ctx, "POST", "/two-fa/info")
	if err == nil || lease != nil {
		t.Fatal("closed database admitted mutation")
	}
	lease, err = lock.Acquire(ctx, true, true)
	if err != nil {
		t.Fatal("database failure leaked shared lease")
	}
	_ = lease.Close()
}

const djangoLockProbe = `import sys
from django.conf import settings
settings.configure(DATA_RESTORE_LOCK_PATH=sys.argv[1])
from instance.restore.locking import operation_lock,Busy
try:
    with operation_lock(exclusive=True):
        assert sys.argv[2]=='open','exclusive Django lock unexpectedly succeeded'
except Busy:
    assert sys.argv[2]=='blocked','exclusive Django lock unexpectedly failed'
`

func TestRestoreLockCrossProcess(t *testing.T) {
	python := os.Getenv("ASSOZETA_CONTRACT_PYTHON")
	if python == "" {
		t.Fatal("run accounts.py --only restore-admission")
	}
	root, err := filepath.Abs("../../..")
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	path := filepath.Join(t.TempDir(), "lifecycle.lock")
	lock := repository.NewRestoreFileLock(path)
	probe := func(want string) {
		t.Helper()
		cmd := exec.CommandContext(ctx, python, "-c", djangoLockProbe, path, want)
		cmd.Env = append(os.Environ(), "PYTHONPATH="+filepath.Join(root, "BE"))
		if output, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("Django flock probe failed: %v\n%s", err, output)
		}
	}
	one, err := lock.Acquire(ctx, false, true)
	if err != nil {
		t.Fatal(err)
	}
	two, err := lock.Acquire(ctx, false, true)
	if err != nil {
		t.Fatal("shared leases conflict")
	}
	probe("blocked")
	_ = one.Close()
	probe("blocked")
	_ = two.Close()
	probe("open")
	if _, err := os.Stat(path); err != nil {
		t.Fatal("release removed the shared lock inode")
	}
	startHolder := func(cmd *exec.Cmd) {
		t.Helper()
		stdout, err := cmd.StdoutPipe()
		if err != nil {
			t.Fatal(err)
		}
		stdin, err := cmd.StdinPipe()
		if err != nil {
			t.Fatal(err)
		}
		cmd.Stderr = os.Stderr
		if err := cmd.Start(); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() {
			_ = stdin.Close()
			if cmd.ProcessState == nil {
				_ = cmd.Process.Kill()
				_ = cmd.Wait()
			}
		})
		ready := make(chan string, 1)
		go func() { line, _ := bufio.NewReader(stdout).ReadString('\n'); ready <- strings.TrimSpace(line) }()
		select {
		case line := <-ready:
			if line != "restore-lease-ready" {
				t.Fatal("lock child did not become ready")
			}
		case <-ctx.Done():
			t.Fatal("lock child timed out")
		}
	}
	// The unchanged Django lock blocks Go; killing its holder releases the inode.
	cmd := exec.CommandContext(ctx, python, "-u", "-c", `import sys
from django.conf import settings
settings.configure(DATA_RESTORE_LOCK_PATH=sys.argv[1])
from instance.restore.locking import operation_lock
with operation_lock(exclusive=True):
    print('restore-lease-ready',flush=True)
    sys.stdin.readline()
`, path)
	cmd.Env = append(os.Environ(), "PYTHONPATH="+filepath.Join(root, "BE"))
	startHolder(cmd)
	if lease, err := lock.Acquire(ctx, false, true); lease != nil || !errors.Is(err, domain.ErrRestoreBusy) {
		t.Fatal("Go ignored exclusive Django lease")
	}
	_ = cmd.Process.Kill()
	_ = cmd.Wait()
	probe("open")
	// The Go process has no chance to run deferred cleanup after SIGKILL.
	cmd = exec.CommandContext(ctx, os.Args[0], "-test.run=^TestRestoreLockProcessChild$")
	cmd.Env = append(os.Environ(), "ASSOZETA_RESTORE_LOCK_CHILD="+path)
	startHolder(cmd)
	probe("blocked")
	_ = cmd.Process.Kill()
	_ = cmd.Wait()
	probe("open")
	if lease, err := repository.NewRestoreFileLock("").Acquire(ctx, false, true); lease != nil || !errors.Is(err, domain.ErrRestoreLockRequired) {
		t.Fatal("required missing lock was accepted")
	}
	if lease, err := repository.NewRestoreFileLock(filepath.Dir(path)).Acquire(ctx, false, false); lease != nil || !errors.Is(err, domain.ErrRestoreVolumeUnavailable) {
		t.Fatal("unavailable lock volume was accepted")
	}
	lease, err := repository.NewRestoreFileLock("").Acquire(ctx, false, false)
	if err != nil {
		t.Fatal(err)
	}
	_ = lease.Close()
}

func TestRestoreLockProcessChild(t *testing.T) {
	path := os.Getenv("ASSOZETA_RESTORE_LOCK_CHILD")
	if path == "" {
		return
	}
	lease, err := repository.NewRestoreFileLock(path).Acquire(context.Background(), false, true)
	if err != nil {
		t.Fatal(err)
	}
	defer lease.Close()
	fmt.Println("restore-lease-ready")
	_, _ = io.Copy(io.Discard, os.Stdin)
}
