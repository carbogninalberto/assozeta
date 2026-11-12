//go:build staffboardintegration

package repository

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jmoiron/sqlx"
)

// The parent adapter owns the migrated database and ORM-seeded fixture; these
// tests never create schema or use a default DSN.
type staffBoardFixture struct {
	Owner              string            `json:"owner"`
	Association        string            `json:"association"`
	Denomination       string            `json:"denomination"`
	Collaborator       string            `json:"collaborator"`
	ForeignAssociation string            `json:"foreign_association"`
	Rows               map[string]string `json:"rows"`
	Bulk               int               `json:"bulk"`
}

type staffBoardCounter struct{ queries atomic.Int64 }

func (c *staffBoardCounter) TraceQueryStart(ctx context.Context, _ *pgx.Conn, _ pgx.TraceQueryStartData) context.Context {
	c.queries.Add(1)
	return ctx
}
func (c *staffBoardCounter) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

func staffBoardLive(t *testing.T) (*sqlx.DB, *staffBoardCounter, staffBoardFixture) {
	t.Helper()
	cfg, err := pgx.ParseConfig(os.Getenv("ASSOZETA_STAFF_BOARD_DSN"))
	if err != nil || cfg.Database == "" || cfg.User != "contracts" {
		t.Fatal("private parent-owned database required")
	}
	counter := &staffBoardCounter{}
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	cfg.Tracer = counter
	db := sqlx.NewDb(database.OpenDB(*cfg), "pgx")
	t.Cleanup(func() { _ = db.Close() })
	var fixture staffBoardFixture
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_STAFF_BOARD_FIXTURE"))
	if err != nil || json.Unmarshal(raw, &fixture) != nil || fixture.Owner == "" || len(fixture.Rows) < 5 {
		t.Fatal("parent fixture required", err)
	}
	return db, counter, fixture
}

func TestStaffBoardRepositoryListLive(t *testing.T) {
	db, counter, fixture := staffBoardLive(t)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	before := counter.queries.Load()
	messages, err := NewStaffBoard(db).StaffBoardMessages(ctx, fixture.Owner)
	if err != nil {
		t.Fatal(err)
	}
	// Association lookup plus one joined query, independent of row count.
	if queries := counter.queries.Load() - before; queries != 2 {
		t.Fatalf("queries=%d", queries)
	}
	// pinned first, then newest; the foreign association's row is excluded.
	want := []string{fixture.Rows["pinned_old"], fixture.Rows["new"], fixture.Rows["deleted_author"], fixture.Rows["to_delete"]}
	if len(messages) != len(want)+fixture.Bulk {
		t.Fatalf("rows=%d", len(messages))
	}
	for i, id := range want {
		if messages[i].ID != id || messages[i].AssociationID != fixture.Association {
			t.Fatalf("order[%d]=%s want %s", i, messages[i].ID, id)
		}
	}
	for _, message := range messages {
		if message.ID == fixture.Rows["foreign"] {
			t.Fatal("foreign association row listed")
		}
	}
	first, deleted := messages[0], messages[2]
	if first.Author == nil || first.Author.ID != fixture.Collaborator || first.Author.FirstName != "Mario" ||
		first.AssociationOwnerID != fixture.Owner || first.AssociationDenomination != fixture.Denomination || !first.Pinned {
		t.Fatalf("joined row %+v", first)
	}
	var document any
	if json.Unmarshal(first.Document, &document) != nil || document.(map[string]any)["type"] != "doc" {
		t.Fatalf("document %s", first.Document)
	}
	if deleted.Author != nil || deleted.Document != nil {
		t.Fatalf("deleted-author row %+v", deleted)
	}
	if row := dto.StaffBoardMessageRowFrom(deleted, fixture.Owner); row.AuthorName != "Utente eliminato" || row.Author != nil {
		t.Fatalf("projection %+v", row)
	}
}

func TestStaffBoardRepositoryMutationsLive(t *testing.T) {
	db, _, fixture := staffBoardLive(t)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	repo := NewStaffBoard(db)
	begin := func() domain.StaffBoardTransaction {
		tx, err := repo.BeginStaffBoardMutations(ctx)
		if err != nil {
			t.Fatal(err)
		}
		return tx
	}
	tx := begin()
	defer tx.Rollback()
	role, active, deleted, err := tx.OwnerForUpdate(ctx, fixture.Owner)
	if err != nil || role != domain.RoleAssociation || !active || deleted {
		t.Fatal("owner lock", role, active, deleted, err)
	}
	association, err := tx.AssociationForOwner(ctx, fixture.Owner, nil)
	if err != nil || association != fixture.Association {
		t.Fatal(association, err)
	}
	at := time.Date(2026, 10, 27, 9, 8, 7, 654321000, time.UTC)
	id := uuid.NewString()
	document := []byte(`{"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Scritto da Go è"}]}]}`)
	if err = tx.InsertStaffBoardMessage(ctx, domain.StaffBoardWrite{ID: id, AssociationID: association,
		AuthorID: fixture.Collaborator, Content: "Scritto da Go è", Document: document, Pinned: true, At: at}); err != nil {
		t.Fatal(err)
	}
	inserted, err := tx.StaffBoardMessageForUpdate(ctx, id, association)
	if err != nil || inserted.Content != "Scritto da Go è" || !inserted.Pinned || inserted.Author == nil ||
		inserted.Author.ID != fixture.Collaborator || !inserted.CreatedAt.Equal(at) || !inserted.UpdatedAt.Equal(at) {
		t.Fatalf("inserted %+v %v", inserted, err)
	}
	later := at.Add(time.Second)
	pinned := false
	if err = tx.UpdateStaffBoardMessage(ctx, id, association, domain.StaffBoardChange{Pinned: &pinned, At: later}); err != nil {
		t.Fatal(err)
	}
	updated, err := tx.StaffBoardMessageForUpdate(ctx, id, association)
	if err != nil || updated.Pinned || updated.Content != inserted.Content || string(updated.Document) != string(inserted.Document) ||
		!updated.UpdatedAt.Equal(later) || !updated.CreatedAt.Equal(at) {
		t.Fatalf("pin-only update rewrote other columns %+v %v", updated, err)
	}
	if err = tx.DeleteStaffBoardMessage(ctx, fixture.Rows["to_delete"], association); err != nil {
		t.Fatal(err)
	}
	// The outbox event and its notification exist only after commit.
	listener, err := pgx.Connect(ctx, os.Getenv("ASSOZETA_STAFF_BOARD_DSN"))
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close(context.Background())
	if _, err = listener.Exec(ctx, "LISTEN assozeta_go_realtime"); err != nil {
		t.Fatal(err)
	}
	var eventsBefore int
	if err = db.GetContext(ctx, &eventsBefore, `SELECT count(*) FROM public.assozeta_go_realtime_events`); err != nil {
		t.Fatal(err)
	}
	if err = tx.AppendRealtimeEvent(ctx, domain.RealtimeStaffBoardChanged, association); err != nil {
		t.Fatal(err)
	}
	early, cancelEarly := context.WithTimeout(ctx, 300*time.Millisecond)
	if _, err = listener.WaitForNotification(early); err == nil {
		t.Fatal("notification delivered before commit")
	}
	cancelEarly()
	if err = tx.Commit(); err != nil {
		t.Fatal(err)
	}
	notification, err := listener.WaitForNotification(ctx)
	if err != nil || notification.Channel != "assozeta_go_realtime" {
		t.Fatal("notification after commit", err)
	}
	var event struct {
		ID      int64  `db:"id"`
		Version int    `db:"envelope_version"`
		Kind    string `db:"kind"`
		Target  string `db:"target_id"`
		Pending bool   `db:"pending"`
	}
	if err = db.GetContext(ctx, &event, `SELECT id,envelope_version,kind,target_id::text AS target_id,
 delivered_at IS NULL AND failed_code IS NULL AS pending FROM public.assozeta_go_realtime_events ORDER BY id DESC LIMIT 1`); err != nil {
		t.Fatal(err)
	}
	if event.Version != 1 || event.Kind != "staff_board_changed" || event.Target != association || !event.Pending ||
		notification.Payload != strconv.FormatInt(event.ID, 10) {
		t.Fatalf("outbox event %+v payload %q", event, notification.Payload)
	}

	// Foreign rows are invisible to every write path.
	tx = begin()
	foreign := fixture.Rows["foreign"]
	if _, err = tx.StaffBoardMessageForUpdate(ctx, foreign, association); !errors.Is(err, domain.ErrStaffBoardNotFound) {
		t.Fatal("foreign lookup", err)
	}
	content := "x"
	if err = tx.UpdateStaffBoardMessage(ctx, foreign, association, domain.StaffBoardChange{Content: &content, At: later}); !errors.Is(err, domain.ErrStaffBoardNotFound) {
		t.Fatal("foreign update", err)
	}
	if err = tx.DeleteStaffBoardMessage(ctx, foreign, association); !errors.Is(err, domain.ErrStaffBoardNotFound) {
		t.Fatal("foreign delete", err)
	}
	_ = tx.Rollback()

	// Like Django's ASCII-escaped JSONField write, a lone surrogate is rejected
	// by PostgreSQL jsonb and nothing persists.
	tx = begin()
	bad := uuid.NewString()
	if err = tx.AppendRealtimeEvent(ctx, domain.RealtimeStaffBoardChanged, association); err != nil {
		t.Fatal(err)
	}
	err = tx.InsertStaffBoardMessage(ctx, domain.StaffBoardWrite{ID: bad, AssociationID: association, AuthorID: fixture.Owner,
		Content: "x", Document: []byte(`{"type": "doc", "t": "\ud800"}`), At: at})
	_ = tx.Rollback()
	var eventsAfter int
	if e := db.GetContext(ctx, &eventsAfter, `SELECT count(*) FROM public.assozeta_go_realtime_events`); e != nil || eventsAfter != eventsBefore+1 {
		t.Fatal("rolled-back event persisted", eventsAfter, eventsBefore, e)
	}
	if err == nil {
		t.Fatal("lone surrogate document accepted")
	}
	var count int
	if err = db.GetContext(ctx, &count, `SELECT count(*) FROM communications_staffboardmessage WHERE staff_board_message_id=$1`, bad); err != nil || count != 0 {
		t.Fatal("rejected write persisted", count, err)
	}

	receipt, _ := json.Marshal(map[string]any{"go_written": id, "deleted": fixture.Rows["to_delete"],
		"created_at": at.Format(time.RFC3339Nano), "updated_at": later.Format(time.RFC3339Nano)})
	if err = os.WriteFile(filepath.Join(os.Getenv("ASSOZETA_STAFF_BOARD_RECEIPTS"), "mutations.json"), receipt, 0o600); err != nil {
		t.Fatal(err)
	}
}
