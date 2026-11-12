//go:build collaboratorintegration

package repository_test

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

type collaboratorTrace struct {
	mu      sync.Mutex
	sql     []string
	started chan uint32
}

func (t *collaboratorTrace) TraceQueryStart(ctx context.Context, connection *pgx.Conn, data pgx.TraceQueryStartData) context.Context {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.sql = append(t.sql, data.SQL)
	if t.started != nil && strings.HasPrefix(data.SQL, "SELECT") {
		select {
		case t.started <- connection.PgConn().PID():
		default:
		}
	}
	return ctx
}

func (t *collaboratorTrace) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

func collaboratorDB(t *testing.T, trace *collaboratorTrace) *sqlx.DB {
	t.Helper()
	dsn := os.Getenv("ASSOZETA_COLLABORATOR_DSN")
	config, err := pgx.ParseConfig(dsn)
	if dsn == "" || err != nil || config.Database != "assozeta_contract_accounts" || config.User != "contracts" || !strings.HasPrefix(config.Host, "/tmp/az-accounts-") {
		t.Fatal("requires actual private collaborator fixture")
	}
	config.DefaultQueryExecMode = pgx.QueryExecModeExec
	if trace != nil {
		config.Tracer = trace
	}
	db := sqlx.NewDb(stdlib.OpenDB(*config), "pgx")
	db.SetMaxOpenConns(4)
	t.Cleanup(func() { db.Close() })
	return db
}
func TestCollaboratorBlockedReadCancellationLive(t *testing.T) {
	control := collaboratorDB(t, nil)
	trace := &collaboratorTrace{started: make(chan uint32, 1)}
	reader := collaboratorDB(t, trace)
	blocker, err := control.BeginTxx(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer blocker.Rollback()
	if _, err = blocker.Exec(`LOCK TABLE bakney_user IN ACCESS EXCLUSIVE MODE`); err != nil {
		t.Fatal(err)
	}
	var blockerPID int
	if err = blocker.Get(&blockerPID, `SELECT pg_backend_pid()`); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan error, 1)
	go func() {
		rows, e := repository.NewCollaboratorList(reader).Collaborators(ctx, uuid.MustParse(collaboratorID(499999)))
		if rows != nil {
			done <- fmt.Errorf("canceled read returned rows")
			return
		}
		done <- e
	}()
	var pid uint32
	select {
	case pid = <-trace.started:
	case e := <-done:
		t.Fatal("read did not block", e)
	case <-time.After(5 * time.Second):
		t.Fatal("reader did not reach PostgreSQL")
	}
	deadline := time.Now().Add(5 * time.Second)
	for {
		var blocked bool
		if err = control.Get(&blocked, `SELECT $1::int=ANY(pg_blocking_pids($2::int))`, blockerPID, pid); err != nil {
			t.Fatal(err)
		}
		if blocked {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("actual PostgreSQL wait not observed")
		}
		time.Sleep(10 * time.Millisecond)
	}
	cancel()
	select {
	case err = <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("blocked read ignored cancellation")
	}
	deadline = time.Now().Add(5 * time.Second)
	for {
		var state struct{ Idle, Unblocked, Held bool }
		err = control.Get(&state, `SELECT NOT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=$1 AND (state IS DISTINCT FROM 'idle' OR xact_start IS NOT NULL)) AS idle,cardinality(pg_blocking_pids($1::int))=0 AS unblocked,EXISTS(SELECT 1 FROM pg_locks WHERE pid=$2 AND relation='bakney_user'::regclass AND mode='AccessExclusiveLock' AND granted) AS held`, pid, blockerPID)
		if err != nil {
			t.Fatal(err)
		}
		if !state.Held {
			t.Fatal("blocker released before proof")
		}
		if state.Idle && state.Unblocked && reader.Stats().InUse == 0 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("backend/transaction/pool not released before blocker")
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Log("Actual PostgreSQL wait observed; canceled reader idle/gone with no transaction, no wait and pool InUse=0 while blocker remained held")
	if err = blocker.Rollback(); err != nil {
		t.Fatal(err)
	}
	if _, err = repository.NewCollaboratorList(reader).Collaborators(context.Background(), uuid.MustParse(collaboratorID(499999))); err != nil {
		t.Fatal("pool did not recover", err)
	}
}

func collaboratorID(n int) string { return fmt.Sprintf("a1000000-0000-4000-8000-%012d", n) }
func TestCollaboratorBoundedScopedReadLive(t *testing.T) {
	for _, count := range []int{0, 1, 32, 128} {
		t.Run(fmt.Sprint(count), func(t *testing.T) {
			trace := &collaboratorTrace{}
			db := collaboratorDB(t, trace)
			tx, err := db.BeginTxx(context.Background(), nil)
			if err != nil {
				t.Fatal(err)
			}
			defer tx.Rollback()
			// Clone complete actual-schema rows through one typed lateral expansion.
			result, err := tx.Exec(`INSERT INTO bakney_user SELECT expanded.* FROM (SELECT * FROM bakney_user ORDER BY user_id LIMIT 1)template CROSS JOIN generate_series(1,$1::integer)n CROSS JOIN LATERAL jsonb_populate_record(NULL::bakney_user,to_jsonb(template)||jsonb_build_object('user_id',('a1000000-0000-4000-8000-'||lpad((620000+n)::text,12,'0')),'username','collaborator-owned-'||n,'email','collaborator-owned-'||n||'@example.test','first_name','Owned','last_name','Person','connected_user_id',$2::text,'deleted',false,'is_active',false,'role',2,'collaborator_permissions',jsonb_build_object('typed',jsonb_build_array(true,1,1.25)))) expanded`, count, collaboratorID(610003))
			if err != nil {
				t.Fatal(err)
			}
			affected, _ := result.RowsAffected()
			if affected != int64(count) {
				t.Fatalf("seeded %d instead of %d", affected, count)
			}
			result, err = tx.Exec(`INSERT INTO application_collaborationinvites SELECT expanded.* FROM (SELECT * FROM application_collaborationinvites ORDER BY collaboration_invite_id LIMIT 1)template CROSS JOIN generate_series(1,$1::integer)n CROSS JOIN LATERAL jsonb_populate_record(NULL::application_collaborationinvites,to_jsonb(template)||jsonb_build_object('collaboration_invite_id',('a1000000-0000-4000-8000-'||lpad((630000+n)::text,12,'0')),'user_id',$2::text,'accepted',true,'token','owned-expired-token','expiration_date','2020-01-01T00:00:00Z'))expanded`, count, collaboratorID(610003))
			if err != nil {
				t.Fatal(err)
			}
			affected, _ = result.RowsAffected()
			if affected != int64(count) {
				t.Fatalf("seeded invites %d instead of %d", affected, count)
			}
			if _, err = tx.Exec(`SET TRANSACTION READ ONLY`); err != nil {
				t.Fatal(err)
			}
			trace.mu.Lock()
			trace.sql = nil
			trace.mu.Unlock()
			reader := service.NewCollaboratorList(repository.NewCollaboratorList(tx))
			identity := domain.ResolvedIdentity{ScopeUser: &domain.Account{ID: collaboratorID(610003), Role: 1}}
			data, err := reader.Read(context.Background(), identity, nil)
			if err != nil {
				t.Fatal(err)
			}
			rows := data.([]any)
			if len(rows) != 2*count {
				t.Fatalf("got %d instead of %d", len(rows), 2*count)
			}
			for i, row := range rows {
				_, isInvite := row.(dto.CollaborationInvite)
				if isInvite != (i >= count) {
					t.Fatal("users/invites order changed")
				}
			}
			trace.mu.Lock()
			queries := append([]string(nil), trace.sql...)
			trace.sql = nil
			trace.mu.Unlock()
			if len(queries) != 2 {
				t.Fatalf("list queries=%d", len(queries))
			}
			for _, query := range queries {
				if !strings.HasPrefix(query, "SELECT ") {
					t.Fatal("unexpected write")
				}
			}
			id := collaboratorID(620001)
			detail, err := reader.Read(context.Background(), identity, &id)
			if err != nil {
				t.Fatal(err)
			}
			trace.mu.Lock()
			queries = append([]string(nil), trace.sql...)
			trace.sql = nil
			trace.mu.Unlock()
			if len(queries) != 1 {
				t.Fatalf("detail queries=%d", len(queries))
			}
			if count > 0 && detail.(dto.Collaborator).ID.String() != id {
				t.Fatal("wrong detail")
			}
			foreign := collaboratorID(612090)
			detail, err = reader.Read(context.Background(), identity, &foreign)
			if err != nil || detail.(dto.Collaborator).ID != nil {
				t.Fatal("foreign owner leaked", err)
			}
			deleted := collaboratorID(612040)
			identity.ScopeUser.ID = collaboratorID(610001)
			detail, err = reader.Read(context.Background(), identity, &deleted)
			if err != nil || detail.(dto.Collaborator).ID != nil {
				t.Fatal("deleted row leaked", err)
			}
		})
	}
}
