//go:build messageintegration

package repository

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

// Parent owns the full migrated database and independently authored seed. These
// tests never create schema, resolve authentication, or use a default DSN.
type messageTraceKey struct{}
type messageExecution struct {
	sql      string
	args     []any
	complete bool
	err      error
}
type messageTracer struct {
	mu      sync.Mutex
	queries []*messageExecution
}

func (tr *messageTracer) TraceQueryStart(ctx context.Context, _ *pgx.Conn, data pgx.TraceQueryStartData) context.Context {
	if ctx.Value(messageTraceKey{}) != true {
		return ctx
	}
	args := data.Args
	// pgx v5.9.2 stdlib.QueryContext prepends this driver option. pgx traces
	// before consuming it; it is not a SQL parameter and must not be rebound
	// through database/sql during EXPLAIN. Preserve every actual SQL argument.
	if len(args) > 0 {
		if _, ok := args[0].(pgx.QueryResultFormatsByOID); ok {
			args = args[1:]
		}
	}
	q := &messageExecution{sql: data.SQL, args: append([]any(nil), args...)}
	tr.mu.Lock()
	tr.queries = append(tr.queries, q)
	tr.mu.Unlock()
	return context.WithValue(ctx, messageTraceKey{}, q)
}
func (tr *messageTracer) TraceQueryEnd(ctx context.Context, _ *pgx.Conn, data pgx.TraceQueryEndData) {
	if q, ok := ctx.Value(messageTraceKey{}).(*messageExecution); ok {
		tr.mu.Lock()
		q.complete, q.err = true, data.Err
		tr.mu.Unlock()
	}
}
func (tr *messageTracer) take(t *testing.T) []*messageExecution {
	t.Helper()
	tr.mu.Lock()
	defer tr.mu.Unlock()
	queries := tr.queries
	tr.queries = nil
	for _, q := range queries {
		if !q.complete || q.err != nil {
			t.Fatal("repository execution did not complete successfully")
		}
	}
	return queries
}

// No database needed: exercise the documented stdlib -> pgx tracer boundary,
// including controls that would catch dropping the first real SQL parameter.
// The live plan test runs this before it opens its private database.
func messageTraceArgumentsControl(t *testing.T) {
	t.Helper()
	for _, params := range [][]any{{messageID(760000)}, {messageID(770000), messageID(761000)}} {
		for _, withOption := range []bool{false, true} {
			tr := &messageTracer{}
			args := append([]any(nil), params...)
			if withOption {
				args = append([]any{pgx.QueryResultFormatsByOID{2950: 0}}, args...)
			}
			ctx := context.WithValue(context.Background(), messageTraceKey{}, true)
			ctx = tr.TraceQueryStart(ctx, nil, pgx.TraceQueryStartData{SQL: "trace control", Args: args})
			tr.TraceQueryEnd(ctx, nil, pgx.TraceQueryEndData{})
			// The trace must own its parameter slice after the driver returns.
			args[len(args)-1] = "changed driver slice"
			queries := tr.take(t)
			if len(queries) != 1 || queries[0].sql != "trace control" || !reflect.DeepEqual(queries[0].args, params) {
				t.Fatal("trace argument separation control failed")
			}
			if len(tr.take(t)) != 0 {
				t.Fatal("trace argument control did not drain")
			}
		}
	}
}

func messagePrivateDB(t *testing.T, tracer *messageTracer) (*sqlx.DB, context.Context) {
	t.Helper()
	dsn := os.Getenv("ASSOZETA_MESSAGE_REPOSITORY_DSN")
	if dsn == "" {
		t.Fatal("ASSOZETA_MESSAGE_REPOSITORY_DSN is required; integration must not skip")
	}
	cfg, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal("invalid private message repository DSN")
	}
	host := filepath.Clean(cfg.Host)
	if !filepath.IsAbs(host) || !strings.HasPrefix(filepath.Base(host), "az-history-") ||
		(filepath.Dir(host) != "/tmp" && filepath.Dir(host) != "/private/tmp") ||
		cfg.Database != "assozeta_contract_historical" || cfg.User != "contracts" || len(cfg.Fallbacks) != 0 {
		t.Fatal("explicit helper-owned Unix-socket database required")
	}
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	cfg.RuntimeParams["timezone"] = "UTC"
	cfg.RuntimeParams["application_name"] = "message-repository-acceptance"
	if tracer != nil {
		cfg.Tracer = tracer
	}
	db := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)
	t.Cleanup(func() {
		if err := db.Close(); err != nil {
			t.Error("private pool close failed")
		}
	})
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	t.Cleanup(cancel)
	if err := db.PingContext(ctx); err != nil {
		t.Fatal("private database unavailable")
	}
	return db, ctx
}
func messageID(n int) string      { return fmt.Sprintf("a1000000-0000-4000-8000-%012d", n) }
func messageUUID(n int) uuid.UUID { return uuid.MustParse(messageID(n)) }
func messageTime(hour, minute, second, micros int) time.Time {
	return time.Date(2026, 10, 23, hour, minute, second, micros*1000, time.UTC)
}
func messagePointer[T any](v T) *T { return &v }

// pgx can decode timestamptz with time.Local; compare instants and nullability,
// not time.Location pointer identity or the host machine's timezone.
func messageSame(a, b reflect.Value) bool {
	if !a.IsValid() || !b.IsValid() {
		return a.IsValid() == b.IsValid()
	}
	if a.Type() != b.Type() {
		return false
	}
	if a.Type() == reflect.TypeOf(time.Time{}) {
		return a.Interface().(time.Time).Equal(b.Interface().(time.Time))
	}
	switch a.Kind() {
	case reflect.Pointer:
		if a.IsNil() || b.IsNil() {
			return a.IsNil() == b.IsNil()
		}
		return messageSame(a.Elem(), b.Elem())
	case reflect.Slice:
		if a.IsNil() != b.IsNil() || a.Len() != b.Len() {
			return false
		}
		for i := 0; i < a.Len(); i++ {
			if !messageSame(a.Index(i), b.Index(i)) {
				return false
			}
		}
		return true
	case reflect.Struct:
		for i := 0; i < a.NumField(); i++ {
			if !messageSame(a.Field(i), b.Field(i)) {
				return false
			}
		}
		return true
	default:
		return reflect.DeepEqual(a.Interface(), b.Interface())
	}
}

func messageEqual(t *testing.T, actual, expected any) {
	t.Helper()
	if !messageSame(reflect.ValueOf(actual), reflect.ValueOf(expected)) {
		t.Fatalf("independently authored records differ: got %#v; want %#v", actual, expected)
	}
}
func messageRollback(t *testing.T, tx *sqlx.Tx) {
	t.Helper()
	t.Cleanup(func() {
		if err := tx.Rollback(); err != nil && !errors.Is(err, sql.ErrTxDone) {
			t.Error("fixture rollback failed")
		}
	})
}
func messageReceipt(t *testing.T, name string, data any) {
	t.Helper()
	dir := os.Getenv("ASSOZETA_MESSAGE_REPOSITORY_RECEIPTS")
	if dir == "" {
		t.Fatal("private receipt directory required")
	}
	content, err := json.Marshal(data)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, name+".json"), content, 0600); err != nil {
		t.Fatal(err)
	}
}

func TestMessageRepositoryHydrationLive(t *testing.T) {
	db, ctx := messagePrivateDB(t, nil)
	tx, err := db.BeginTxx(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	messageRollback(t, tx)
	// Additional fields missing from the portable seed: all timestamps non-null,
	// empty versus null delivery strings, and literal quotes/HTML. Fixed PK,
	// collision fails, transaction rollback also covers every failure path.
	_, err = tx.ExecContext(ctx, `INSERT INTO communications_messagetransaction
 (message_transaction_id,message_id,recipient,sent_on,delivered_on,opened_on,clicked_on,bounced_on,bounced_reason,bounced_type,bounced_status)
 VALUES ($1,$2,$3,$4,$4,$4,$4,$4,$5,$6,$7)`, messageID(703099), messageID(702001),
		`literal <&> "'@example.test`, messageTime(13, 0, 0, 654321), `reason <&> "'`, "", "5.0.0")
	if err != nil {
		t.Fatal(err)
	}
	repo := NewMessageReads(tx)
	rows, err := repo.ListMessages(ctx, messageID(700001))
	if err != nil {
		t.Fatal(err)
	}
	messageEqual(t, rows, []domain.MessageReadRecord{
		{ID: messageUUID(702002), AssociationID: messageUUID(701001), Type: "INSIDE_APP", Message: "Post senza data"},
		{ID: messageUUID(702001), AssociationID: messageUUID(701001), Type: "EMAIL", Message: `<p>Prova <strong>A & B</strong> "quoted" 'single'</p>`, Subject: messagePointer("  Quote & attività <2026>  "), CreatedAt: messagePointer(messageTime(8, 9, 10, 123456))},
		{ID: messageUUID(702003), AssociationID: messageUUID(701001), Type: "EMAIL", Message: "Avviso più vecchio", Subject: messagePointer("old"), CreatedAt: messagePointer(messageTime(7, 0, 0, 0))},
		{ID: messageUUID(702006), AssociationID: messageUUID(701001), Type: "EMAIL", Message: "No delivery history", Subject: messagePointer("empty"), CreatedAt: messagePointer(messageTime(4, 0, 0, 0))},
	})
	escaped := dto.MessageReadFromRecord(rows[1])
	messageEqual(t, escaped.Message, "&lt;p&gt;Prova &lt;strong&gt;A &amp; B&lt;/strong&gt; &quot;quoted&quot; &#x27;single&#x27;&lt;/p&gt;")
	messageEqual(t, escaped.Subject, "  Quote &amp; attività &lt;2026&gt;  ")
	messageEqual(t, dto.MessageReadFromRecord(rows[0]).Subject, "None")
	deliveries, err := repo.MessageTransactions(ctx, messageID(700001), messageID(702001))
	if err != nil {
		t.Fatal(err)
	}
	stamp := messageTime(13, 0, 0, 654321)
	messageEqual(t, deliveries, []domain.MessageTransactionReadRecord{
		{ID: messageUUID(703099), MessageID: messageUUID(702001), Recipient: `literal <&> "'@example.test`, SentOn: stamp, DeliveredOn: &stamp, OpenedOn: &stamp, ClickedOn: &stamp, BouncedOn: &stamp, BouncedReason: messagePointer(`reason <&> "'`), BouncedType: messagePointer(""), BouncedStatus: messagePointer("5.0.0")},
		{ID: messageUUID(703001), MessageID: messageUUID(702001), Recipient: "first@example.test", SentOn: messageTime(12, 0, 0, 123456), DeliveredOn: messagePointer(messageTime(12, 0, 1, 123456)), OpenedOn: messagePointer(messageTime(12, 1, 0, 1))},
		{ID: messageUUID(703002), MessageID: messageUUID(702001), Recipient: "second@example.test", SentOn: messageTime(12, 0, 0, 123456)},
		{ID: messageUUID(703003), MessageID: messageUUID(702001), Recipient: "bounced@example.test", SentOn: messageTime(11, 0, 0, 0), BouncedOn: messagePointer(messageTime(11, 0, 2, 0)), BouncedReason: messagePointer("Mailbox full"), BouncedType: messagePointer("permanent"), BouncedStatus: messagePointer("5.2.2")},
	})
	for _, id := range []int{702002, 702004, 702005, 702006, 799999} {
		got, err := repo.MessageTransactions(ctx, messageID(700001), messageID(id))
		if err != nil || got == nil || len(got) != 0 {
			t.Fatal("empty/foreign/unsupported/missing detail leaked", id, err)
		}
	}
	foreign, err := repo.ListMessages(ctx, messageID(700007))
	if err != nil {
		t.Fatal(err)
	}
	messageEqual(t, foreign, []domain.MessageReadRecord{{ID: messageUUID(702004), AssociationID: messageUUID(701002), Type: "EMAIL", Message: "Foreign private body", Subject: messagePointer("foreign"), CreatedAt: messagePointer(messageTime(6, 0, 0, 0))}})
	foreignDetail, err := repo.MessageTransactions(ctx, messageID(700007), messageID(702004))
	if err != nil {
		t.Fatal(err)
	}
	messageEqual(t, foreignDetail, []domain.MessageTransactionReadRecord{{ID: messageUUID(703004), MessageID: messageUUID(702004), Recipient: "foreign@example.test", SentOn: messageTime(10, 30, 0, 0)}})
	empty, err := repo.ListMessages(ctx, messageID(760000))
	if err != nil || empty == nil || len(empty) != 0 {
		t.Fatal("empty owned association differs", err)
	}
	// Collaborator IDs are deliberately not resolved here. The service accepts
	// identity.ScopeUser.ID after identity resolution; repository accepts owner.
	for _, owner := range []int{700002, 700003, 700005, 700006, 799999} {
		_, err := repo.ListMessages(ctx, messageID(owner))
		if !errors.Is(err, domain.ErrAssociationNotFound) {
			t.Fatal("owner predicate differs", owner, err)
		}
		_, err = repo.MessageTransactions(ctx, messageID(owner), messageID(702001))
		if !errors.Is(err, domain.ErrAssociationNotFound) {
			t.Fatal("detail owner predicate differs", owner, err)
		}
	}
	messageReceipt(t, "hydration", map[string]any{"own_messages": 4, "own_transactions": 4, "foreign_messages": 1, "empty_details": 5, "missing_owners": 5, "literal_and_null_fields": true, "resolved_owner_boundary": true})
}

type messageIndex struct {
	Relation       string   `json:"relation"`
	Columns        []string `json:"columns"`
	Unique         bool     `json:"unique"`
	Primary        bool     `json:"primary"`
	NameHash       string   `json:"name_sha256"`
	DefinitionHash string   `json:"definition_sha256"`
}

func messageHash(value string) string { return fmt.Sprintf("%x", sha256.Sum256([]byte(value))) }
func messageIndexes(t *testing.T, ctx context.Context, tx *sqlx.Tx) []messageIndex {
	t.Helper()
	rows, err := tx.QueryxContext(ctx, `SELECT c.relname,ic.relname,i.indisunique,i.indisprimary,
 pg_get_indexdef(i.indexrelid), array_to_json(ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY k(attnum,n)
 JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum ORDER BY k.n))::text
 FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid JOIN pg_namespace ns ON ns.oid=c.relnamespace
 JOIN pg_class ic ON ic.oid=i.indexrelid JOIN pg_am am ON am.oid=ic.relam
 WHERE ns.nspname='public' AND c.relname IN ('application_sportassociation','communications_message','communications_messagetransaction')
 AND i.indisvalid AND i.indisready AND i.indpred IS NULL AND i.indexprs IS NULL AND am.amname='btree'
 AND (SELECT attname FROM pg_attribute WHERE attrelid=i.indrelid AND attnum=i.indkey[0])
 IN ('user_id','sport_association_id','message_id','message_transaction_id')
 ORDER BY c.relname,ic.relname`)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()
	var result []messageIndex
	found := map[string]bool{}
	for rows.Next() {
		var v messageIndex
		var name, definition, columns string
		if err := rows.Scan(&v.Relation, &name, &v.Unique, &v.Primary, &definition, &columns); err != nil {
			t.Fatal(err)
		}
		if err := json.Unmarshal([]byte(columns), &v.Columns); err != nil {
			t.Fatal(err)
		}
		v.NameHash, v.DefinitionHash = messageHash(name), messageHash(definition)
		if len(v.Columns) > 0 {
			found[v.Relation+":"+v.Columns[0]] = true
		}
		result = append(result, v)
	}
	if err := rows.Err(); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"application_sportassociation:user_id", "communications_message:sport_association_id", "communications_message:message_id", "communications_messagetransaction:message_id"} {
		if !found[key] {
			t.Fatal("required existing leading-column index missing", key)
		}
	}
	return result
}

// Only these safe fields leave the private directory. Never serialize the raw
// plan: filters/index conditions contain literal SQL arguments and private data.
type messagePlanNode struct {
	Node      string  `json:"node"`
	Relation  string  `json:"relation"`
	IndexHash string  `json:"index_sha256"`
	Estimated float64 `json:"estimated_rows"`
	Actual    float64 `json:"actual_rows"`
	Loops     float64 `json:"loops"`
	Hit       float64 `json:"shared_hit_blocks"`
	Read      float64 `json:"shared_read_blocks"`
}
type messagePlan struct {
	Kind     string            `json:"kind"`
	SQLHash  string            `json:"sql_sha256"`
	Expected int               `json:"expected_rows"`
	Nodes    []messagePlanNode `json:"nodes"`
}

func messageExplain(t *testing.T, ctx context.Context, tx *sqlx.Tx, q *messageExecution, kind string, expected int) messagePlan {
	t.Helper()
	var raw []byte
	if err := tx.GetContext(ctx, &raw, "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "+q.sql, q.args...); err != nil {
		t.Fatal(err)
	}
	var plans []map[string]any
	if err := json.Unmarshal(raw, &plans); err != nil || len(plans) != 1 {
		t.Fatal("invalid PostgreSQL JSON plan")
	}
	result := messagePlan{Kind: kind, SQLHash: messageHash(q.sql), Expected: expected}
	var visit func(map[string]any)
	visit = func(n map[string]any) {
		str := func(key string) string { v, _ := n[key].(string); return v }
		num := func(key string) float64 { v, _ := n[key].(float64); return v }
		v := messagePlanNode{Node: str("Node Type"), Relation: str("Relation Name"), Estimated: num("Plan Rows"), Actual: num("Actual Rows"), Loops: num("Actual Loops"), Hit: num("Shared Hit Blocks"), Read: num("Shared Read Blocks")}
		if name := str("Index Name"); name != "" {
			v.IndexHash = messageHash(name)
		}
		result.Nodes = append(result.Nodes, v)
		children, _ := n["Plans"].([]any)
		for _, child := range children {
			visit(child.(map[string]any))
		}
	}
	root, ok := plans[0]["Plan"].(map[string]any)
	if !ok {
		t.Fatal("missing plan root")
	}
	visit(root)
	if result.Nodes[0].Actual != float64(expected) || result.Nodes[0].Loops != 1 {
		t.Fatal("actual plan cardinality differs from exercised output")
	}
	return result
}

type messagePlanStage struct {
	Owners       int           `json:"owners"`
	Messages     int           `json:"messages"`
	Transactions int           `json:"transactions"`
	Plans        []messagePlan `json:"plans"`
}

func TestMessageRepositoryExecutedPlansLive(t *testing.T) {
	messageTraceArgumentsControl(t)
	tracer := &messageTracer{}
	db, ctx := messagePrivateDB(t, tracer)
	tx, err := db.BeginTxx(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	messageRollback(t, tx)
	indexes := messageIndexes(t, ctx, tx)
	var stages []messagePlanStage
	previous := 0
	for _, owners := range []int{8, 24, 48} {
		// All writes have fixed UUIDs in an owned range and remain uncommitted.
		// 128 allowed messages plus 128 deliveries per owner provide contrast:
		// at 48 owners a target scope selects 1/48 of 6144 exercised rows.
		for owner := previous; owner < owners; owner++ {
			base := 770000 + owner*128
			_, err = tx.ExecContext(ctx, `INSERT INTO communications_message
 (message_id,sport_association_id,type,message,subject,created_at)
 SELECT ('a1000000-0000-4000-8000-'||lpad(($2::int+n)::text,12,'0'))::uuid,$1,
 CASE WHEN n%2=0 THEN 'EMAIL' ELSE 'INSIDE_APP' END,'plan <&> fixture',NULL,
 '2026-10-23 00:00:00+00'::timestamptz+n*interval '1 second' FROM generate_series(0,127) n`, messageID(761000+owner), base)
			if err != nil {
				t.Fatal(err)
			}
			_, err = tx.ExecContext(ctx, `INSERT INTO communications_messagetransaction
 (message_transaction_id,message_id,recipient,sent_on)
 SELECT ('a1000000-0000-4000-8000-'||lpad(($2::int+n)::text,12,'0'))::uuid,$1,
 'plan@example.test','2026-10-23 01:00:00+00'::timestamptz FROM generate_series(0,127) n`, messageID(base), 800000+owner*128)
			if err != nil {
				t.Fatal(err)
			}
		}
		previous = owners
		// Natural planner choices with current statistics; no planner switches.
		for _, table := range []string{"application_sportassociation", "communications_message", "communications_messagetransaction"} {
			if _, err = tx.ExecContext(ctx, "ANALYZE "+table); err != nil {
				t.Fatal(err)
			}
		}
		stage := messagePlanStage{Owners: owners, Messages: 6 + owners*128, Transactions: 6 + owners*128}
		var messages, transactions, scopes int
		if err = tx.GetContext(ctx, &messages, "SELECT count(*) FROM communications_message"); err != nil {
			t.Fatal(err)
		}
		if err = tx.GetContext(ctx, &transactions, "SELECT count(*) FROM communications_messagetransaction"); err != nil {
			t.Fatal(err)
		}
		if err = tx.GetContext(ctx, &scopes, `SELECT count(DISTINCT sport_association_id) FROM communications_message WHERE message_id >= $1 AND message_id <= $2`, messageID(770000), messageID(770000+owners*128-1)); err != nil {
			t.Fatal(err)
		}
		if messages != stage.Messages || transactions != stage.Transactions || scopes != owners {
			t.Fatal("owned fixture cardinality differs")
		}
		for _, owner := range []int{0, owners - 1} {
			base := 770000 + owner*128
			repo := NewMessageReads(tx)
			observedCtx := context.WithValue(ctx, messageTraceKey{}, true)
			rows, err := repo.ListMessages(observedCtx, messageID(760000+owner))
			if err != nil || len(rows) != 128 {
				t.Fatal("representative list failed", err)
			}
			for i, row := range rows {
				typ := "EMAIL"
				if (127-i)%2 != 0 {
					typ = "INSIDE_APP"
				}
				messageEqual(t, row, domain.MessageReadRecord{ID: messageUUID(base + 127 - i), AssociationID: messageUUID(761000 + owner), Type: typ, Message: "plan <&> fixture", CreatedAt: messagePointer(messageTime(0, 0, 0, 0).Add(time.Duration(127-i) * time.Second))})
			}
			details, err := repo.MessageTransactions(observedCtx, messageID(760000+owner), messageID(base))
			if err != nil || len(details) != 128 {
				t.Fatal("representative detail failed", err)
			}
			for i, row := range details {
				messageEqual(t, row, domain.MessageTransactionReadRecord{ID: messageUUID(800000 + owner*128 + i), MessageID: messageUUID(base), Recipient: "plan@example.test", SentOn: messageTime(1, 0, 0, 0)})
			}
			queries := tracer.take(t)
			if len(queries) != 4 {
				t.Fatal("repository query count differs")
			}
			for i, q := range queries {
				kind, expected := "association", 1
				args := []any{messageID(760000 + owner)}
				if i == 1 {
					kind, expected = "list", 128
					args = []any{messageID(761000 + owner)}
				}
				if i == 3 {
					kind, expected = "detail", 128
					args = []any{messageID(base), messageID(761000 + owner)}
				}
				if !reflect.DeepEqual(q.args, args) {
					t.Fatal("executed owner/association bindings differ")
				}
				stage.Plans = append(stage.Plans, messageExplain(t, ctx, tx, q, kind, expected))
			}
			foreign, err := repo.MessageTransactions(ctx, messageID(760000+owner), messageID(770000+((owner+1)%owners)*128))
			if err != nil || len(foreign) != 0 {
				t.Fatal("synthetic foreign detail leaked", err)
			}
		}
		stages = append(stages, stage)
	}
	messageReceipt(t, "plans", map[string]any{"indexes": indexes, "stages": stages, "planner_forced": false, "executed_sql_traced": true})
}

func TestMessageRepositoryBlockedCancellationLive(t *testing.T) {
	db, ctx := messagePrivateDB(t, nil)    // Single-slot reader pool exposes leaked leases.
	control, _ := messagePrivateDB(t, nil) // Separate connection observes actual waits.
	blocker, err := control.BeginTxx(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer blocker.Rollback()
	var blockerPID int
	if err = blocker.GetContext(ctx, &blockerPID, "SELECT pg_backend_pid()"); err != nil {
		t.Fatal(err)
	}
	if _, err = blocker.ExecContext(ctx, "LOCK TABLE communications_message IN ACCESS EXCLUSIVE MODE"); err != nil {
		t.Fatal(err)
	}
	// A third pool is necessary: control's single slot is held by the blocker.
	observer, _ := messagePrivateDB(t, nil)
	readCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	done := make(chan error, 1)
	go func() { _, err := NewMessageReads(db).ListMessages(readCtx, messageID(700001)); done <- err }()
	var readerPID int
	deadline := time.NewTimer(8 * time.Second)
	defer deadline.Stop()
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
	for readerPID == 0 {
		err = observer.GetContext(ctx, &readerPID, `SELECT COALESCE(min(pid),0) FROM pg_stat_activity
 WHERE datname=current_database() AND application_name='message-repository-acceptance'
 AND query=$1 AND state='active' AND wait_event_type='Lock' AND $2=ANY(pg_blocking_pids(pid))`, messageReadsListSQL, blockerPID)
		if err != nil {
			t.Fatal(err)
		}
		if readerPID != 0 {
			break
		}
		select {
		case <-done:
			t.Fatal("read ended before observed table-lock wait")
		case <-deadline.C:
			t.Fatal("message SELECT lock wait was not observed")
		case <-ticker.C:
		}
	}
	started := time.Now()
	cancel()
	select {
	case err = <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatal("blocked message SELECT did not return context cancellation", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("canceled SELECT did not return promptly while blocker held")
	}
	elapsed := time.Since(started).Milliseconds()
	// Confirm the blocker transaction still holds its granted exclusive lock.
	var held bool
	if err = observer.GetContext(ctx, &held, `SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=$1
 AND relation='communications_message'::regclass AND mode='AccessExclusiveLock' AND granted)`, blockerPID); err != nil || !held {
		t.Fatal("blocker released before cancellation proof", err)
	}
	for {
		var idle bool
		err = observer.GetContext(ctx, &idle, `SELECT NOT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=$1
 AND (state IS DISTINCT FROM 'idle' OR xact_start IS NOT NULL OR wait_event_type='Lock'))`, readerPID)
		if err != nil {
			t.Fatal(err)
		}
		if idle && db.Stats().InUse == 0 {
			break
		}
		select {
		case <-deadline.C:
			t.Fatal("canceled reader backend/pool remained occupied")
		case <-ticker.C:
		}
	}
	if err = blocker.Rollback(); err != nil {
		t.Fatal(err)
	}
	followCtx, stop := context.WithTimeout(ctx, 3*time.Second)
	defer stop()
	rows, err := NewMessageReads(db).ListMessages(followCtx, messageID(700001))
	if err != nil || len(rows) != 4 || db.Stats().InUse != 0 {
		t.Fatal("pool did not recover after cancellation", err)
	}
	messageEqual(t, []uuid.UUID{rows[0].ID, rows[1].ID, rows[2].ID, rows[3].ID}, []uuid.UUID{messageUUID(702002), messageUUID(702001), messageUUID(702003), messageUUID(702006)})
	messageReceipt(t, "cancellation", map[string]any{"lock_wait_observed": true, "blocker_held_at_return": true, "context_canceled": true, "return_ms": elapsed, "pool_in_use_before_release": 0, "pool_in_use_after_followup": 0, "followup_rows": 4})
}
