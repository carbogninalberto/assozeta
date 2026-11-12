//go:build modelauditintegration

package repository

import (
	"context"
	"os"
	"testing"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

// Exercise the actual persistence SQL against PostgreSQL. Temporary tables
// isolate this content-type selection test; this is not full-schema parity.
func TestModelAuditContentTypeNamespace(t *testing.T) {
	db, err := sqlx.Connect("pgx", os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN"))
	if err != nil {
		t.Fatal("private PostgreSQL unavailable")
	}
	defer db.Close()
	tx, err := db.BeginTxx(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	for _, statement := range []string{
		`CREATE TEMP TABLE django_content_type(id integer PRIMARY KEY, app_label text NOT NULL, model text NOT NULL, UNIQUE(app_label,model)) ON COMMIT DROP`,
		`INSERT INTO django_content_type VALUES(1,'application','message'),(2,'communications','message')`,
		`CREATE TEMP TABLE auditlog_logentry(id bigserial PRIMARY KEY, content_type_id integer REFERENCES django_content_type(id), object_pk text, object_id bigint, object_repr text, serialized_data jsonb, action integer, changes_text text, changes jsonb, actor_id uuid, cid text, remote_addr inet, remote_port integer, timestamp timestamptz, additional_data jsonb, actor_email text) ON COMMIT DROP`,
	} {
		if _, err := tx.Exec(statement); err != nil {
			t.Fatal(err)
		}
	}
	event := modelAuditEntry{Model: "message", Key: "00000000-0000-4000-8000-000000000001", Representation: "namespace-fixture", Changes: map[string][2]string{"message": {"None", "saved"}}, At: time.Now().UTC()}
	if err := appendModelAudit(context.Background(), tx, event); err != nil {
		t.Fatal(err)
	}
	if err := appendModelAuditForApp(context.Background(), tx, "communications", event); err != nil {
		t.Fatal(err)
	}
	var ids []int
	if err := tx.Select(&ids, `SELECT content_type_id FROM auditlog_logentry ORDER BY id`); err != nil {
		t.Fatal(err)
	}
	if len(ids) != 2 || ids[0] != 1 || ids[1] != 2 {
		t.Fatalf("wrong namespace selection: %v", ids)
	}
	for _, label := range []string{"missing", "communications' OR true --"} {
		if err := appendModelAuditForApp(context.Background(), tx, label, event); err == nil {
			t.Fatal("absent namespace accepted")
		}
	}
	var count int
	if err := tx.Get(&count, `SELECT count(*) FROM auditlog_logentry`); err != nil || count != 2 {
		t.Fatal("rejected namespace changed audits", err)
	}
}
