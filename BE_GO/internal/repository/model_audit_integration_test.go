//go:build modelauditintegration

package repository

import (
	"context"
	"database/sql"
	"os"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
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

// Exercise the index fallbacks against PostgreSQL: a log entry whose payment
// row the resolver cannot reload is indexed from its changes, else from its
// actor, always through SportAssociation.objects (not deleted, .first()).
func TestModelAuditIndexFallbackResolution(t *testing.T) {
	db, err := sqlx.Connect("pgx", os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN"))
	if err != nil {
		t.Fatal("private PostgreSQL unavailable")
	}
	defer db.Close()
	ctx := context.Background()
	tx, err := db.BeginTxx(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	const (
		actor           = "00000000-0000-4000-8000-0000000a0001"
		otherActor      = "00000000-0000-4000-8000-0000000a0002"
		associationOld  = "00000000-0000-4000-8000-0000000b0001"
		associationOwn  = "00000000-0000-4000-8000-0000000b0002"
		associationGone = "00000000-0000-4000-8000-0000000b0003"
	)
	for _, statement := range []string{
		`CREATE TEMP TABLE django_content_type(id integer PRIMARY KEY, app_label text NOT NULL, model text NOT NULL, UNIQUE(app_label,model)) ON COMMIT DROP`,
		`INSERT INTO django_content_type VALUES(1,'application','payment')`,
		`CREATE TEMP TABLE auditlog_logentry(id bigserial PRIMARY KEY, content_type_id integer REFERENCES django_content_type(id), object_pk text, object_id bigint, object_repr text, serialized_data jsonb, action integer, changes_text text, changes jsonb, actor_id uuid, cid text, remote_addr inet, remote_port integer, timestamp timestamptz, additional_data jsonb, actor_email text) ON COMMIT DROP`,
		`CREATE TEMP TABLE application_sportassociation(sport_association_id uuid PRIMARY KEY, user_id uuid NOT NULL, deleted boolean NOT NULL) ON COMMIT DROP`,
		`CREATE TEMP TABLE application_auditlogindex(audit_log_index_id uuid PRIMARY KEY, log_entry_id bigint, sport_association_id uuid, resolution_path text, created_at timestamptz) ON COMMIT DROP`,
	} {
		if _, err := tx.Exec(statement); err != nil {
			t.Fatal(err)
		}
	}
	for _, row := range []struct {
		association, user string
		deleted           bool
	}{
		{associationOld, otherActor, false},
		{associationOwn, actor, false},
		{associationGone, actor, true},
	} {
		if _, err := tx.Exec(`INSERT INTO application_sportassociation VALUES($1::uuid,$2::uuid,$3)`,
			row.association, row.user, row.deleted); err != nil {
			t.Fatal(err)
		}
	}
	stale := associationOwn
	actorID := actor
	audit := func(changes map[string][2]string, eventActor *string, fallback bool, association *string) (string, string, bool) {
		t.Helper()
		event := modelAuditEntry{Model: "payment", Key: "00000000-0000-4000-8000-0000000c0001", Representation: "Payment object",
			Action: 1, Changes: changes, Context: domain.LoginAuditContext{ActorID: eventActor},
			AssociationID: association, IndexFallback: fallback, At: time.Now().UTC()}
		if err := appendModelAuditForApp(ctx, tx, "application", event); err != nil {
			t.Fatal(err)
		}
		var id int64
		if err := tx.Get(&id, `SELECT max(id) FROM auditlog_logentry`); err != nil {
			t.Fatal(err)
		}
		var row struct {
			Association sql.NullString `db:"sport_association_id"`
			Path        sql.NullString `db:"resolution_path"`
		}
		err := tx.Get(&row, `SELECT sport_association_id::text AS sport_association_id, resolution_path
  FROM application_auditlogindex WHERE log_entry_id=$1`, id)
		if err == sql.ErrNoRows {
			return "", "", false
		}
		if err != nil {
			t.Fatal(err)
		}
		return row.Association.String, row.Path.String, true
	}
	for _, test := range []struct {
		name                      string
		changes                   map[string][2]string
		eventActor                *string
		fallback                  bool
		association               *string
		wantAssociation, wantPath string
		wantIndexed               bool
	}{
		{name: "changes old value", changes: map[string][2]string{"sport_association": {"None", associationOld}},
			eventActor: &actorID, fallback: true, association: &stale, wantAssociation: associationOld,
			wantPath: "changes_json.sport_association", wantIndexed: true},
		{name: "old value first", changes: map[string][2]string{"sport_association": {associationOld, associationOwn}},
			fallback: true, wantAssociation: associationOld, wantPath: "changes_json.sport_association", wantIndexed: true},
		{name: "actor fallback skips deleted", changes: map[string][2]string{"sport_association": {"None", "None"}},
			eventActor: &actorID, fallback: true, wantAssociation: associationOwn, wantPath: "actor.sport_association", wantIndexed: true},
		{name: "invalid changes value falls to actor", changes: map[string][2]string{"sport_association": {"not-a-uuid", "None"}},
			eventActor: &actorID, fallback: true, wantAssociation: associationOwn, wantPath: "actor.sport_association", wantIndexed: true},
		{name: "nothing resolves", changes: map[string][2]string{}, fallback: true},
		{name: "direct index unchanged", changes: map[string][2]string{}, association: &stale,
			wantAssociation: associationOwn, wantPath: "direct", wantIndexed: true},
	} {
		gotAssociation, gotPath, indexed := audit(test.changes, test.eventActor, test.fallback, test.association)
		if gotAssociation != test.wantAssociation || gotPath != test.wantPath || indexed != test.wantIndexed {
			t.Errorf("%s: association=%q path=%q indexed=%v, want %q %q %v", test.name,
				gotAssociation, gotPath, indexed, test.wantAssociation, test.wantPath, test.wantIndexed)
		}
	}
}
