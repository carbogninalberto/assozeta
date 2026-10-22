//go:build jobsintegration

package migrations

import (
	"encoding/json"
	"os"
	"reflect"
	"sync"
	"testing"

	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

func TestOwnershipMigrationPostgres(t *testing.T) {
	dsn := os.Getenv("ASSOZETA_JOB_MIGRATION_DSN")
	if dsn == "" {
		t.Fatal("private migration PostgreSQL fixture required; no skipped integration")
	}
	db, e := sqlx.Connect("pgx", dsn)
	if e != nil {
		t.Fatal("private database unavailable")
	}
	defer db.Close()
	execute := func(query string) {
		t.Helper()
		if _, err := db.Exec(query); err != nil {
			t.Fatal(err)
		}
	}
	// Reconstruct the previous operational migration version, not Django's schema.
	first, e := files.ReadFile("000001_durable_jobs.up.sql")
	if e != nil {
		t.Fatal(e)
	}
	execute(string(first))
	execute(`CREATE TABLE assozeta_go_schema_migrations(version bigint PRIMARY KEY,dirty boolean NOT NULL);INSERT INTO assozeta_go_schema_migrations VALUES(1,false);
 CREATE TABLE django_migrations(id bigint PRIMARY KEY,app text,name text);INSERT INTO django_migrations VALUES(17,'fixture','preserve');
 INSERT INTO assozeta_go_jobs(id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at)
 VALUES('11111111-1111-4111-8111-111111111111','00000000-0000-0000-0000-000000000000','fixture.pending',1,'durable-key','{"exact":9007199254740993}',3,clock_timestamp());
 INSERT INTO assozeta_go_jobs(id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at,state,attempts,lease_owner,lease_token,lease_until)
 VALUES('22222222-2222-4222-8222-222222222222','00000000-0000-0000-0000-000000000000','fixture.running',1,'leased-key','{}',3,clock_timestamp(),'running',1,'historical-worker','33333333-3333-4333-8333-333333333333',clock_timestamp()+interval '1 hour')`)
	snapshot := func() []map[string]json.RawMessage {
		t.Helper()
		var raw []string
		if err := db.Select(&raw, `SELECT row_to_json(j)::text FROM assozeta_go_jobs j ORDER BY id`); err != nil {
			t.Fatal(err)
		}
		result := make([]map[string]json.RawMessage, len(raw))
		for i, text := range raw {
			if err := json.Unmarshal([]byte(text), &result[i]); err != nil {
				t.Fatal(err)
			}
			delete(result[i], "ownership_generation")
		}
		return result
	}
	before := snapshot()
	t.Run("additive upgrade preserves pending leased jobs and Django history", func(t *testing.T) {
		var workers sync.WaitGroup
		for range 4 {
			workers.Add(1)
			go func() {
				defer workers.Done()
				if err := Apply(dsn); err != nil {
					t.Error(err)
				}
			}()
		}
		workers.Wait()
		if !reflect.DeepEqual(snapshot(), before) {
			t.Fatal("migration changed durable intent or existing lease")
		}
		var count int
		if e = db.Get(&count, `SELECT count(*) FROM assozeta_go_jobs WHERE ownership_generation=0`); e != nil || count != 2 {
			t.Fatal("historical jobs gained an authorized generation", e)
		}
		if e = db.Get(&count, `SELECT count(*) FROM django_migrations WHERE id=17 AND app='fixture' AND name='preserve'`); e != nil || count != 1 {
			t.Fatal("Django history changed", e)
		}
		if e = db.Get(&count, `SELECT count(*) FROM assozeta_go_job_ownership WHERE singleton=1 AND runtime='django' AND generation=1`); e != nil || count != 1 {
			t.Fatal("migration activated Go", e)
		}
	})
	t.Run("reapplying does not reset ownership or generation", func(t *testing.T) {
		// Trusted fixture state only; there is no runtime ownership setter.
		execute(`UPDATE assozeta_go_job_ownership SET runtime='go',generation=7 WHERE singleton=1`)
		if e = Apply(dsn); e != nil {
			t.Fatal(e)
		}
		var count int
		if e = db.Get(&count, `SELECT count(*) FROM assozeta_go_job_ownership WHERE runtime='go' AND generation=7`); e != nil || count != 1 {
			t.Fatal("migration reset ownership", e)
		}
	})
	t.Run("down refusal retains ownership and permanent job keys", func(t *testing.T) {
		down, err := files.ReadFile("000002_job_ownership.down.sql")
		if err != nil {
			t.Fatal(err)
		}
		if _, err = db.Exec(string(down)); err == nil {
			t.Fatal("destructive down accepted")
		}
		if !reflect.DeepEqual(snapshot(), before) {
			t.Fatal("down refusal changed jobs")
		}
		var count int
		if e = db.Get(&count, `SELECT count(*) FROM assozeta_go_job_ownership WHERE runtime='go' AND generation=7`); e != nil || count != 1 {
			t.Fatal("down changed ownership", e)
		}
	})
	t.Run("dirty history fails closed without force or data changes", func(t *testing.T) {
		execute(`UPDATE assozeta_go_schema_migrations SET dirty=true`)
		if e = Apply(dsn); e == nil {
			t.Fatal("dirty migration was silently forced")
		}
		if !reflect.DeepEqual(snapshot(), before) {
			t.Fatal("dirty migration changed jobs")
		}
	})
}
