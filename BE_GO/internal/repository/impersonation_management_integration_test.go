//go:build impersonationintegration

package repository_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

type managementTrace struct {
	mu      sync.Mutex
	sql     []string
	started chan uint32
}

func (t *managementTrace) TraceQueryStart(ctx context.Context, connection *pgx.Conn, data pgx.TraceQueryStartData) context.Context {
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

func TestImpersonationAccessAuditProjectionLive(t *testing.T) {
	db := managementDB(t, nil)
	tx, err := db.BeginTxx(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	managementSeed(t, tx, 3)
	if _, err = tx.Exec(`UPDATE bakney_user SET role=1,is_superuser=(user_id=$2::uuid) WHERE user_id IN ($1::uuid,$2::uuid)`, managementID(400001), managementID(400003)); err != nil {
		t.Fatal(err)
	}
	association := managementID(410001)
	_, err = tx.Exec(`INSERT INTO application_sportassociation SELECT (jsonb_populate_record(NULL::application_sportassociation,to_jsonb(template)||jsonb_build_object('sport_association_id',$1::text,'user_id',$2::text,'denomination','Audit owner','deleted',false))).* FROM (SELECT * FROM application_sportassociation ORDER BY sport_association_id LIMIT 1)template`, association, managementID(400001))
	if err != nil {
		t.Fatal(err)
	}
	store := repository.NewImpersonationManagement(tx)
	person, err := store.ImpersonationPersonIncludingDeleted(context.Background(), managementID(400002))
	if err != nil {
		t.Fatal(err)
	}
	cid, ip, email := "owned-correlation", "127.0.0.1", "must-not-populate@example.test"
	port := json.Number("8080")
	at := time.Date(2026, 10, 24, 10, 0, 0, 123456000, time.UTC)
	for _, admin := range []bool{false, true} {
		actor := domain.Account{ID: managementID(400001), Active: true, Role: 1, Email: email}
		var scope *string = &association
		if admin {
			actor.ID = managementID(400003)
			actor.Superuser = true
			scope = nil
		}
		event := domain.ImpersonationTransition{Actor: actor, Target: person, SessionID: managementID(430001), Action: "started", ScopeAssociationID: scope, At: at, Audit: domain.LoginAuditContext{ActorEmail: &email, CorrelationID: &cid, RemoteAddress: &ip, RemotePort: &port}}
		if err = store.AppendImpersonationTransition(context.Background(), event); err != nil {
			t.Fatal(err)
		}
		var row struct {
			ID                         int64
			Action                     int
			ObjectPK, ObjectRepr       string
			ActorEmail, SerializedData *string
			Changes, AdditionalData    []byte
			RemoteAddr                 string
			RemotePort                 int
			CID                        string
			Timestamp                  time.Time
		}
		err = tx.Get(&row, `SELECT id,action,object_pk AS objectpk,object_repr AS objectrepr,actor_email AS actoremail,serialized_data::text AS serializeddata,changes,additional_data AS additionaldata,host(remote_addr::inet) AS remoteaddr,remote_port AS remoteport,cid,timestamp FROM auditlog_logentry WHERE object_pk=$1 AND actor_id=$2 ORDER BY id DESC LIMIT 1`, person.ID, actor.ID)
		if err != nil || row.Action != 3 || row.ObjectPK != person.ID || row.ObjectRepr != "name: Owned Target" || row.ActorEmail != nil || row.SerializedData != nil || row.CID != cid || row.RemoteAddr != ip || row.RemotePort != 8080 || !row.Timestamp.Equal(at) {
			t.Fatal("ACCESS projection differs", err, row)
		}
		var additional map[string]any
		var changes map[string][]string
		if json.Unmarshal(row.AdditionalData, &additional) != nil || json.Unmarshal(row.Changes, &changes) != nil {
			t.Fatal("invalid audit JSON")
		}
		if len(additional) != 6 || additional["actor_id"] != actor.ID || additional["target_user_id"] != person.ID || additional["target_role"] != float64(2) || additional["impersonation_session"] != event.SessionID || len(changes) != 1 || len(changes["impersonation"]) != 2 || changes["impersonation"][0] != "" || changes["impersonation"][1] != "started" {
			t.Fatal("ACCESS payload differs", additional, changes)
		}
		var indices []struct{ Association, Path string }
		if err = tx.Select(&indices, `SELECT sport_association_id AS association,resolution_path AS path FROM application_auditlogindex WHERE log_entry_id=$1`, row.ID); err != nil {
			t.Fatal(err)
		}
		if admin {
			if len(indices) != 0 || additional["administrator_id"] != actor.ID || additional["association_id"] != nil {
				t.Fatal("admin index/data differs")
			}
		} else if len(indices) != 1 || indices[0].Association != association || indices[0].Path != "actor.sport_association" || additional["administrator_id"] != nil || additional["association_id"] != association {
			t.Fatal("owner index/data differs", indices)
		}
	}
}

func TestImpersonationBlockedReadCancellationLive(t *testing.T) {
	control := managementDB(t, nil)
	trace := &managementTrace{started: make(chan uint32, 1)}
	reader := managementDB(t, trace)
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
		rows, _, e := repository.NewImpersonationManagement(reader).ListImpersonationPeople(ctx, managementID(499999), "", domain.ImpersonationListFilter{Sort: "username", Limit: 25, Offset: "0"})
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
	if _, _, err = repository.NewImpersonationManagement(reader).ListImpersonationPeople(context.Background(), managementID(499999), "", domain.ImpersonationListFilter{Sort: "username", Limit: 25, Offset: "0"}); err != nil {
		t.Fatal("pool did not recover", err)
	}
}

func TestImpersonationExistingAuditIndexSkipsResolverLive(t *testing.T) {
	trace := &managementTrace{}
	db := managementDB(t, trace)
	tx, err := db.BeginTxx(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	managementSeed(t, tx, 2)
	association := managementID(410001)
	_, err = tx.Exec(`INSERT INTO application_sportassociation SELECT (jsonb_populate_record(NULL::application_sportassociation,to_jsonb(template)||jsonb_build_object('sport_association_id',$1::text,'user_id',$2::text,'denomination','Existing index owner','deleted',false))).* FROM (SELECT * FROM application_sportassociation ORDER BY sport_association_id LIMIT 1)template`, association, managementID(400001))
	if err != nil {
		t.Fatal(err)
	}
	// An owned database trigger supplies the existing index before the append
	// returns from INSERT. This tests the actual early guard without replacing
	// SQL or the business method with a mock. All DDL/data rolls back together.
	_, err = tx.Exec(`CREATE FUNCTION impersonation_owned_preindex() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 INSERT INTO application_auditlogindex(audit_log_index_id,log_entry_id,sport_association_id,resolution_path,created_at)
 VALUES('00000000-0000-4000-8000-000000440001',NEW.id,'00000000-0000-4000-8000-000000410001','owned-precreated',clock_timestamp()); RETURN NEW; END $$`)
	if err != nil {
		t.Fatal(err)
	}
	_, err = tx.Exec(`CREATE TRIGGER impersonation_owned_preindex AFTER INSERT ON auditlog_logentry FOR EACH ROW WHEN (NEW.object_pk='00000000-0000-4000-8000-000000400002') EXECUTE FUNCTION impersonation_owned_preindex()`)
	if err != nil {
		t.Fatal(err)
	}
	store := repository.NewImpersonationManagement(tx)
	target, err := store.ImpersonationPersonIncludingDeleted(context.Background(), managementID(400002))
	if err != nil {
		t.Fatal(err)
	}
	trace.take()
	err = store.AppendImpersonationTransition(context.Background(), domain.ImpersonationTransition{Actor: domain.Account{ID: managementID(400001), Active: true, Role: 1}, Target: target, SessionID: managementID(430001), Action: "started", ScopeAssociationID: &association, At: time.Now()})
	queries := trace.take()
	if err != nil || len(queries) != 2 || !strings.HasPrefix(queries[0], "INSERT INTO auditlog_logentry") || !strings.Contains(queries[1], "application_auditlogindex") {
		t.Fatal("existing index did not stop resolver/insertion", len(queries), err)
	}
	var path string
	if err = tx.Get(&path, `SELECT resolution_path FROM application_auditlogindex WHERE audit_log_index_id=$1`, managementID(440001)); err != nil || path != "owned-precreated" {
		t.Fatal("existing index changed", path, err)
	}
}
func (*managementTrace) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}
func (t *managementTrace) take() []string {
	t.mu.Lock()
	defer t.mu.Unlock()
	result := t.sql
	t.sql = nil
	return result
}
func managementID(n int) string { return fmt.Sprintf("00000000-0000-4000-8000-%012d", n) }
func managementDB(t *testing.T, trace *managementTrace) *sqlx.DB {
	t.Helper()
	dsn := os.Getenv("ASSOZETA_IMPERSONATION_DSN")
	config, err := pgx.ParseConfig(dsn)
	if dsn == "" || err != nil || config.Database != "assozeta_contract_accounts" || config.User != "contracts" || !strings.HasPrefix(config.Host, "/tmp/az-accounts-") {
		t.Fatal("requires actual private impersonation fixture")
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

func managementSeed(t *testing.T, tx *sqlx.Tx, count int) {
	t.Helper()
	_, err := tx.Exec(`INSERT INTO bakney_user SELECT (jsonb_populate_record(NULL::bakney_user,to_jsonb(template)||jsonb_build_object(
 'user_id',('00000000-0000-4000-8000-'||lpad((400000+n)::text,12,'0')),'username','management-owned-'||lpad(n::text,3,'0'),
 'email','management-owned-'||n||'@example.test','first_name','Owned','last_name','Target','is_active',true,'is_superuser',false,'deleted',false,'role',2,'connected_user_id',NULL,'last_login',NULL))).*
 FROM (SELECT * FROM bakney_user ORDER BY user_id LIMIT 1)template CROSS JOIN generate_series(1,$1::integer)n`, count)
	if err != nil {
		t.Fatal(err)
	}
}

func TestImpersonationDirectoryBoundedLive(t *testing.T) {
	for _, count := range []int{1, 8, 32, 100, 128} {
		t.Run(fmt.Sprint(count), func(t *testing.T) {
			trace := &managementTrace{}
			db := managementDB(t, trace)
			tx, err := db.BeginTxx(context.Background(), nil)
			if err != nil {
				t.Fatal(err)
			}
			defer tx.Rollback()
			managementSeed(t, tx, count)
			store := repository.NewImpersonationManagement(tx)
			trace.take()
			rows, total, err := store.ListImpersonationPeople(context.Background(), managementID(499999), "", domain.ImpersonationListFilter{Search: "management-owned-", Sort: "username", Limit: 100, Offset: "0"})
			if err != nil || total != int64(count) || len(rows) != min(count, 100) {
				t.Fatal(total, len(rows), err)
			}
			queries := trace.take()
			if len(queries) != 2 {
				t.Fatal("list queries grow with rows", len(queries))
			}
			for _, query := range queries {
				if !strings.HasPrefix(query, "SELECT") {
					t.Fatal("list executed a write")
				}
			}
			for i, row := range rows {
				if row.ID != managementID(400001+i) || row.Role != 2 || row.LastLogin != nil {
					t.Fatal("projection/order differs", i, row.ID)
				}
			}
			var actorID string
			if err = db.Get(&actorID, `SELECT user_id FROM bakney_user WHERE is_superuser AND is_active AND NOT deleted ORDER BY user_id LIMIT 1`); err != nil {
				t.Fatal(err)
			}
			accounts := repository.NewAccounts(db)
			actor, err := accounts.ByID(context.Background(), actorID)
			if err != nil {
				t.Fatal(err)
			}
			trace.take()
			result, err := service.NewImpersonationManagement(accounts, store, nil, time.Now, nil).List(context.Background(), &actor, domain.RequestObject{{Name: "q", Value: "management-owned-"}, {Name: "pagination[perpage]", Value: "100"}})
			queries = trace.take()
			if err != nil || len(queries) != 3 || result.Meta.Total != int64(count) || len(result.Users) != min(count, 100) {
				t.Fatal("composed service read bound/projection differs", len(queries), result.Meta, err)
			}
			for _, query := range queries {
				if !strings.HasPrefix(query, "SELECT") {
					t.Fatal("composed list wrote data")
				}
			}
			t.Logf("%d eligible users: repository2 actual SELECTs; composed service3 including fresh actor", count)
		})
	}
}

func TestImpersonationEligibilityAndDeletedTargetsLive(t *testing.T) {
	db := managementDB(t, nil)
	tx, err := db.BeginTxx(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	managementSeed(t, tx, 8)
	for _, statement := range []string{
		`UPDATE bakney_user SET role=1 WHERE user_id IN ('00000000-0000-4000-8000-000000400001','00000000-0000-4000-8000-000000400002')`,
		`UPDATE bakney_user SET role=3,connected_user_id='00000000-0000-4000-8000-000000400001' WHERE user_id='00000000-0000-4000-8000-000000400004'`,
		`UPDATE bakney_user SET is_superuser=true WHERE user_id='00000000-0000-4000-8000-000000400006'`,
		`UPDATE bakney_user SET is_active=false WHERE user_id='00000000-0000-4000-8000-000000400007'`,
		`UPDATE bakney_user SET deleted=true WHERE user_id='00000000-0000-4000-8000-000000400008'`,
	} {
		if _, err = tx.Exec(statement); err != nil {
			t.Fatal(err)
		}
	}
	for n := 1; n <= 2; n++ {
		_, err = tx.Exec(`INSERT INTO application_sportassociation SELECT (jsonb_populate_record(NULL::application_sportassociation,to_jsonb(template)||jsonb_build_object('sport_association_id',$1::text,'user_id',$2::text,'denomination',$3::text,'deleted',false))).* FROM (SELECT * FROM application_sportassociation ORDER BY sport_association_id LIMIT 1)template`, managementID(410000+n), managementID(400000+n), fmt.Sprintf("Owned association %d", n))
		if err != nil {
			t.Fatal(err)
		}
		person := 400003
		if n == 2 {
			person = 400005
		}
		_, err = tx.Exec(`INSERT INTO application_associate SELECT (jsonb_populate_record(NULL::application_associate,to_jsonb(template)||jsonb_build_object('associate_id',$1::text,'sport_association_id',$2::text,'user_id',$3::text,'deleted',false))).* FROM (SELECT * FROM application_associate ORDER BY associate_id LIMIT 1)template`, managementID(420000+n), managementID(410000+n), managementID(person))
		if err != nil {
			t.Fatal(err)
		}
	}
	store := repository.NewImpersonationManagement(tx)
	for n := 1; n <= 8; n++ {
		_, err = store.EligibleImpersonationPerson(context.Background(), managementID(400001), managementID(410001), managementID(400000+n))
		allowed := n == 3 || n == 4
		if allowed && err != nil || !allowed && !errors.Is(err, domain.ErrAccountNotFound) {
			t.Fatal("owner eligibility", n, err)
		}
		_, err = store.EligibleImpersonationPerson(context.Background(), managementID(499999), "", managementID(400000+n))
		allowed = n <= 5
		if allowed && err != nil || !allowed && !errors.Is(err, domain.ErrAccountNotFound) {
			t.Fatal("admin eligibility", n, err)
		}
	}
	deleted, err := store.ImpersonationPersonIncludingDeleted(context.Background(), managementID(400008))
	if err != nil || !deleted.Deleted {
		t.Fatal("original_objects lookup lost deleted target", err)
	}
	if _, err = tx.Exec(`UPDATE application_sportassociation SET deleted=true WHERE sport_association_id=$1`, managementID(410001)); err != nil {
		t.Fatal(err)
	}
	if _, err = store.ImpersonationAssociation(context.Background(), managementID(400001)); !errors.Is(err, domain.ErrAssociationNotFound) {
		t.Fatal("deleted association admitted", err)
	}
	if _, err = store.EligibleImpersonationPerson(context.Background(), managementID(499999), "", managementID(400004)); !errors.Is(err, domain.ErrAccountNotFound) {
		t.Fatal("deleted owner association admitted collaborator", err)
	}
}
