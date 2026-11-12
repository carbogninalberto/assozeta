//go:build paymentinfointegration

package repository_test

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

func paymentInfoFixtureID(number int) uuid.UUID {
	return uuid.MustParse(fmt.Sprintf("a1000000-0000-4000-8000-%012d", number))
}

func paymentInfoDB(t *testing.T) *sqlx.DB {
	return paymentInfoTracedDB(t, nil)
}

func paymentInfoTracedDB(t *testing.T, tracer pgx.QueryTracer) *sqlx.DB {
	t.Helper()
	dsn := os.Getenv("ASSOZETA_PAYMENT_INFO_DSN")
	cfg, err := pgx.ParseConfig(dsn)
	if dsn == "" || err != nil || cfg.Database != "assozeta_contract_accounts" || cfg.User != "contracts" || !strings.HasPrefix(cfg.Host, "/tmp/az-accounts-") {
		t.Fatal("requires the live private payment-info fixture from TESTS/adapters/django/accounts.py")
	}
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	cfg.Tracer = tracer
	db := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	db.SetMaxOpenConns(4)
	t.Cleanup(func() { _ = db.Close() })
	return db
}

type paymentInfoQueryTrace struct {
	mu      sync.Mutex
	queries []string
	started chan uint32
}

func (trace *paymentInfoQueryTrace) TraceQueryStart(ctx context.Context, conn *pgx.Conn, data pgx.TraceQueryStartData) context.Context {
	trace.mu.Lock()
	trace.queries = append(trace.queries, data.SQL)
	trace.mu.Unlock()
	if trace.started != nil && strings.HasPrefix(data.SQL, "SELECT") {
		select {
		case trace.started <- conn.PgConn().PID():
		default:
		}
	}
	return ctx
}

func (*paymentInfoQueryTrace) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

func (trace *paymentInfoQueryTrace) take() []string {
	trace.mu.Lock()
	defer trace.mu.Unlock()
	result := append([]string(nil), trace.queries...)
	trace.queries = nil
	return result
}

func paymentInfoRead(t *testing.T, reader domain.PaymentInfoReader, number int, association, group *uuid.UUID) *domain.PaymentInfoRecord {
	t.Helper()
	row, err := reader.ReadPaymentInfo(context.Background(), domain.PaymentInfoLookup{PaymentID: paymentInfoFixtureID(number), AssociationID: association, GroupID: group})
	if err != nil {
		t.Fatal(err)
	}
	return row
}

func paymentInfoString(value *string) any {
	if value == nil {
		return nil
	}
	return *value
}

func TestPaymentInfoHydrationReadOnlyLive(t *testing.T) {
	db := paymentInfoDB(t)
	tx, err := db.BeginTxx(context.Background(), &sql.TxOptions{ReadOnly: true})
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	repo := repository.NewPaymentInfo(tx)
	row := paymentInfoRead(t, repo, 226000, nil, nil)
	if row == nil {
		t.Fatal("existing payment was not hydrated")
	}
	if row.ID != paymentInfoFixtureID(226000) || row.Subject != 0 || paymentInfoString(row.Amount) != "123.40" || row.CreatedAt == nil || !row.CreatedAt.Equal(time.Date(2026, 10, 23, 8, 9, 10, 123456000, time.UTC)) {
		t.Fatalf("payment scalar fields: %+v", row)
	}
	if row.User == nil || paymentInfoString(row.User.FirstName) != "Ada" || paymentInfoString(row.User.LastName) != "Athlete" || paymentInfoString(row.User.Email) != "payment-info-220002@example.test" || paymentInfoString(row.User.Phone) != "+390001" || paymentInfoString(row.User.Username) != "payment-info-220002" || row.User.Role != 2 {
		t.Fatalf("nested user: %+v", row.User)
	}
	if row.Associate == nil || paymentInfoString(row.Associate.FirstName) != "Ada" || paymentInfoString(row.Associate.LastName) != "Member" || paymentInfoString(row.Associate.TaxCode) != "FAMILYCODE" {
		t.Fatalf("nested associate: %+v", row.Associate)
	}
	if row.Association == nil || row.Association.ID != paymentInfoFixtureID(221000) || row.Association.OwnerID == nil || *row.Association.OwnerID != paymentInfoFixtureID(220000) || paymentInfoString(row.Association.Denomination) != "Checkout One" || paymentInfoString(row.Association.TaxCode) != "12345678901" || paymentInfoString(row.Association.CheckoutInfo) != "<p>Pay once &amp; retain receipt</p>" || paymentInfoString(row.CategoryName) != "Own category" {
		t.Fatalf("association/category: %+v / %v", row.Association, row.CategoryName)
	}
	if row.Direct != nil || row.Installment != nil || row.Carnet != nil {
		t.Fatal("non-course payment acquired course candidates")
	}
	null := paymentInfoRead(t, repo, 226001, nil, nil)
	if null.User != nil || null.Associate != nil || null.CategoryName != nil || paymentInfoString(null.Amount) != "0.00" {
		t.Fatalf("null values: %+v", null)
	}
	for _, tc := range []struct {
		id     int
		amount string
	}{{226002, "9999999.99"}, {226013, "0.01"}} {
		if got := paymentInfoRead(t, repo, tc.id, nil, nil); paymentInfoString(got.Amount) != tc.amount {
			t.Fatalf("money %d: %v", tc.id, got.Amount)
		}
	}
	if paymentInfoRead(t, repo, 226003, nil, nil) == nil {
		t.Fatal("archived paid payment hidden")
	}
	if paymentInfoRead(t, repo, 226009, nil, nil) != nil || paymentInfoRead(t, repo, 229999, nil, nil) != nil {
		t.Fatal("deleted/missing payment visible")
	}
	if paymentInfoRead(t, repo, 226010, nil, nil).Association != nil {
		t.Fatal("null association replaced")
	}
	if got := paymentInfoRead(t, repo, 226006, nil, nil); paymentInfoString(got.CategoryName) != "Global deleted category" {
		t.Fatal("deleted global category hidden")
	}
	if got := paymentInfoRead(t, repo, 226007, nil, nil); paymentInfoString(got.CategoryName) != "Foreign category" {
		t.Fatal("foreign category unexpectedly scoped")
	}
	if got := paymentInfoRead(t, repo, 226015, nil, nil); string(got.Course) != "false" {
		t.Fatalf("false stored JSON: %s", got.Course)
	}
	if got := paymentInfoRead(t, repo, 226014, nil, nil); got.CreatedAt == nil || !got.CreatedAt.Equal(time.Date(2026, 10, 23, 6, 9, 10, 123456000, time.UTC)) {
		t.Fatal("timestamp changed")
	}
	names, err := repo.ReadPaymentInfoCategoryNames(context.Background(), []uuid.UUID{paymentInfoFixtureID(225000), paymentInfoFixtureID(225001), paymentInfoFixtureID(225002)})
	if err != nil || !reflect.DeepEqual(names, map[uuid.UUID]string{paymentInfoFixtureID(225000): "Own category", paymentInfoFixtureID(225001): "Foreign category", paymentInfoFixtureID(225002): "Global deleted category"}) {
		t.Fatalf("category hydration: %v %v", names, err)
	}
	// PostgreSQL enforces read-only execution for both repository methods above.
	if err := tx.Commit(); err != nil {
		t.Fatal(err)
	}
}

func TestPaymentInfoScopeAndRelationsLive(t *testing.T) {
	db := paymentInfoDB(t)
	tx, err := db.BeginTxx(context.Background(), &sql.TxOptions{ReadOnly: true})
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	repo := repository.NewPaymentInfo(tx)
	own, foreign, group, otherGroup := paymentInfoFixtureID(221000), paymentInfoFixtureID(221001), paymentInfoFixtureID(222000), paymentInfoFixtureID(222001)
	if paymentInfoRead(t, repo, 226005, nil, nil) == nil || paymentInfoRead(t, repo, 226005, &own, nil) != nil || paymentInfoRead(t, repo, 226005, &foreign, nil) == nil {
		t.Fatal("association scope changed ordinary/scoped visibility")
	}
	if paymentInfoRead(t, repo, 226200, nil, &group) == nil || paymentInfoRead(t, repo, 226200, nil, &otherGroup) != nil || paymentInfoRead(t, repo, 226000, nil, &group) != nil {
		t.Fatal("group predicate mismatch")
	}
	for _, tc := range []struct {
		name                        string
		payment                     int
		scope, group                *uuid.UUID
		direct, installment, carnet int
		title, fee                  any
	}{
		{"direct", 226300, nil, nil, 227100, 0, 0, "Direct course", "56.70"},
		{"installment", 226301, nil, nil, 0, 227200, 0, "Direct course", "56.70"},
		{"disabled foreign carnet", 226302, nil, nil, 0, 0, 227400, "Foreign carnet", "89.10"},
		{"deleted course null fields", 226303, nil, nil, 227101, 0, 0, nil, nil},
		{"deleted direct hidden", 226304, nil, nil, 0, 0, 0, nil, nil},
		{"null carnet", 226305, nil, nil, 0, 0, 227401, nil, nil},
		{"all candidates", 226306, nil, nil, 227103, 227201, 227402, "Direct course", "56.70"},
		{"installment and carnet", 226307, nil, nil, 0, 227202, 227403, "Direct course", "56.70"},
		{"deleted installment parent retained", 226308, nil, nil, 0, 227203, 0, "Direct course", "56.70"},
		{"deleted installment hidden", 226309, nil, nil, 0, 0, 0, nil, nil},
		{"foreign direct ordinary", 226310, nil, &group, 227105, 0, 0, "Foreign course", "10.01"},
		{"foreign direct scoped", 226310, &own, &group, 0, 0, 0, nil, nil},
		{"foreign installment ordinary", 226311, nil, nil, 0, 227205, 0, "Foreign course", "10.01"},
		{"foreign installment group", 226311, nil, &group, 0, 0, 0, nil, nil},
		{"foreign installment scoped", 226311, &own, nil, 0, 0, 0, nil, nil},
		{"ordinary fallback candidates", 226312, nil, nil, 0, 227206, 227404, "Foreign course", "10.01"},
		{"scoped legitimate fallback", 226312, &own, nil, 0, 0, 227404, "Own fallback", "12.30"},
		{"scoped foreign carnet hidden", 226302, &own, nil, 0, 0, 0, nil, nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			row := paymentInfoRead(t, repo, tc.payment, tc.scope, tc.group)
			if row == nil {
				t.Fatal("payment itself must remain visible")
			}
			var selected *domain.PaymentInfoRelation
			for i, relation := range []*domain.PaymentInfoRelation{row.Direct, row.Installment, row.Carnet} {
				want := []int{tc.direct, tc.installment, tc.carnet}[i]
				if want == 0 {
					if relation != nil {
						t.Fatalf("unexpected relation %d: %+v", i, relation)
					}
					continue
				}
				if relation == nil || relation.ID != paymentInfoFixtureID(want) {
					t.Fatalf("relation %d: %+v; want %d", i, relation, want)
				}
				if selected == nil {
					selected = relation
				}
			}
			if selected != nil && tc.payment != 226305 {
				if selected.Offering == nil || paymentInfoString(selected.Offering.Title) != tc.title || paymentInfoString(selected.Offering.Fee) != tc.fee {
					t.Fatalf("offering: %+v", selected.Offering)
				}
			}
			if tc.payment == 226305 && row.Carnet.Offering != nil {
				t.Fatal("null carnet fabricated")
			}
			if tc.payment == 226303 && row.Direct.Offering.Association != nil {
				t.Fatal("null course association fabricated")
			}
		})
	}
	if err := tx.Commit(); err != nil {
		t.Fatal(err)
	}
}

func TestPaymentInfoRawJSONLive(t *testing.T) {
	db := paymentInfoDB(t)
	tx, err := db.BeginTxx(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	meta := `[{"payment_category_id":"a1000000-0000-4000-8000-000000225000","extra":9007199254740993},{"payment_category_id":"a1000000-0000-4000-8000-000000225000","extra":9007199254740995}]`
	course := `{"large":123456789012345678901234567890,"values":[false,null,{},[],""]}`
	if _, err = tx.Exec(`UPDATE application_payment SET meta_payment_categories=$1::jsonb,course=$2::jsonb WHERE payment_id=$3`, meta, course, paymentInfoFixtureID(226000)); err != nil {
		t.Fatal(err)
	}
	row := paymentInfoRead(t, repository.NewPaymentInfo(tx), 226000, nil, nil)
	for _, tc := range []struct {
		got  json.RawMessage
		want string
	}{{row.Meta, meta}, {row.Course, course}} {
		decode := func(data []byte) any {
			d := json.NewDecoder(strings.NewReader(string(data)))
			d.UseNumber()
			var value any
			if err := d.Decode(&value); err != nil {
				t.Fatal(err)
			}
			return value
		}
		if !reflect.DeepEqual(decode(tc.got), decode([]byte(tc.want))) {
			t.Fatalf("JSON changed: %s", tc.got)
		}
	}
	// Related FK reads use the base manager: deleted/inactive related records
	// remain present. Ordinary payment-info policy is deliberately not tightened.
	for _, statement := range []string{
		`UPDATE bakney_user SET deleted=TRUE,is_active=FALSE WHERE user_id IN ('a1000000-0000-4000-8000-000000220000','a1000000-0000-4000-8000-000000220002')`,
		`UPDATE application_associate SET deleted=TRUE WHERE associate_id='a1000000-0000-4000-8000-000000223000'`,
		`UPDATE application_sportassociation SET deleted=TRUE WHERE sport_association_id='a1000000-0000-4000-8000-000000221000'`,
	} {
		if _, err := tx.Exec(statement); err != nil {
			t.Fatal(err)
		}
	}
	row = paymentInfoRead(t, repository.NewPaymentInfo(tx), 226000, nil, nil)
	if row.User == nil || row.Associate == nil || row.Association == nil || row.Association.OwnerID == nil {
		t.Fatalf("related base-manager records hidden: %+v", row)
	}
}

func TestPaymentInfoFirstRelationAfterScopeLive(t *testing.T) {
	db := paymentInfoDB(t)
	tx, err := db.BeginTxx(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	// Add lower-PK foreign candidates to prove filtering happens before .first(),
	// and that ordinary reads preserve the database PK order for each relation.
	for _, fixture := range []struct {
		table, key     string
		source, number int
		changes        map[string]any
	}{
		{"application_coursesubscription", "course_subscription_id", 227100, 227099, map[string]any{"course_id": paymentInfoFixtureID(227002)}},
		{"application_coursesubscriptioninstallment", "course_subscription_installment_id", 227200, 227199, map[string]any{"course_subscription_id": paymentInfoFixtureID(227105)}},
		{"application_carnetsubscription", "carnet_subscription_id", 227404, 227399, map[string]any{"carnet_id_id": paymentInfoFixtureID(227300)}},
	} {
		fixture.changes[fixture.key] = paymentInfoFixtureID(fixture.number)
		payload, err := json.Marshal(fixture.changes)
		if err != nil {
			t.Fatal(err)
		}
		// Table/key are fixed test constants; copy the actual schema's complete row.
		query := `INSERT INTO ` + fixture.table + ` SELECT (jsonb_populate_record(NULL::` + fixture.table + `,to_jsonb(source)||$1::jsonb)).* FROM ` + fixture.table + ` source WHERE ` + fixture.key + `=$2`
		result, err := tx.Exec(query, string(payload), paymentInfoFixtureID(fixture.source))
		if err != nil {
			t.Fatal(err)
		}
		if count, err := result.RowsAffected(); err != nil || count != 1 {
			t.Fatalf("clone fixture: %d %v", count, err)
		}
	}
	repo := repository.NewPaymentInfo(tx)
	own := paymentInfoFixtureID(221000)
	for _, tc := range []struct {
		payment, unscoped, scoped int
		relation                  string
	}{
		{226300, 227099, 227100, "direct"}, {226301, 227199, 227200, "installment"}, {226312, 227399, 227404, "carnet"},
	} {
		for _, scoped := range []bool{false, true} {
			var scope *uuid.UUID
			want := tc.unscoped
			if scoped {
				scope = &own
				want = tc.scoped
			}
			row := paymentInfoRead(t, repo, tc.payment, scope, nil)
			relation := map[string]*domain.PaymentInfoRelation{"direct": row.Direct, "installment": row.Installment, "carnet": row.Carnet}[tc.relation]
			if relation == nil || relation.ID != paymentInfoFixtureID(want) {
				t.Fatalf("%s scoped=%t got=%+v want=%d", tc.relation, scoped, relation, want)
			}
		}
	}
}

func TestPaymentInfoMetadataQueriesBoundedLive(t *testing.T) {
	trace := &paymentInfoQueryTrace{}
	db := paymentInfoTracedDB(t, trace)
	tx, err := db.BeginTxx(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	repo := repository.NewPaymentInfo(tx)
	ids := make([]uuid.UUID, 128)
	idStrings := make([]string, len(ids))
	for i := range ids {
		ids[i] = uuid.MustParse(fmt.Sprintf("a2000000-0000-4000-8000-%012d", i+1))
		idStrings[i] = ids[i].String()
	}
	var collisions int
	if err := tx.Get(&collisions, `SELECT count(*) FROM application_paymentcategory WHERE payment_category_id=ANY($1::uuid[])`, idStrings); err != nil {
		t.Fatal(err)
	}
	if collisions != 0 {
		t.Fatal("private batch fixture ID collision")
	}
	for i, id := range ids {
		var association *uuid.UUID
		if i%3 != 0 {
			value := paymentInfoFixtureID(221000 + i%2)
			association = &value
		}
		if _, err := tx.Exec(`INSERT INTO application_paymentcategory(payment_category_id,name,creation_date,sport_association_id,expense,tax_deductible,archived,type,deleted) VALUES($1,$2,now(),$3,FALSE,FALSE,$4,1,$5)`, id, fmt.Sprintf("Batch %d", i), association, i%2 == 0, i%2 == 1); err != nil {
			t.Fatal(err)
		}
	}
	for _, count := range []int{0, 1, 8, 32, 128} {
		for _, duplicates := range []bool{false, true} {
			t.Run(fmt.Sprintf("distinct-%d-duplicates-%t", count, duplicates), func(t *testing.T) {
				meta := make([]map[string]any, 0, count*2)
				for i, id := range ids[:count] {
					entry := map[string]any{"payment_category_id": id.String(), "amount": "0.01", "extra": json.Number(fmt.Sprintf("900719925474%04d", i+993))}
					meta = append(meta, entry)
					if duplicates {
						meta = append(meta, entry)
					}
				}
				encoded, err := json.Marshal(meta)
				if err != nil {
					t.Fatal(err)
				}
				if _, err = tx.Exec(`UPDATE application_payment SET meta_payment_categories=$1::jsonb WHERE payment_id=$2`, string(encoded), paymentInfoFixtureID(226000)); err != nil {
					t.Fatal(err)
				}
				trace.take()
				row := paymentInfoRead(t, repo, 226000, nil, nil)
				names, err := repo.ReadPaymentInfoCategoryNames(context.Background(), ids[:count])
				queries := trace.take()
				wantQueries := 1
				if count != 0 {
					wantQueries = 2
				}
				if err != nil || len(names) != count || len(queries) != wantQueries {
					t.Fatalf("names=%d queries=%d want=%d error=%v SQL=%v", len(names), len(queries), wantQueries, err, queries)
				}
				for _, query := range queries {
					if !strings.HasPrefix(query, "SELECT") {
						t.Fatalf("non-read statement: %s", query)
					}
				}
				for i, id := range ids[:count] {
					if names[id] != fmt.Sprintf("Batch %d", i) {
						t.Fatalf("lost global/foreign/deleted name for %s", id)
					}
				}
				var stored []map[string]json.RawMessage
				if err := json.Unmarshal(row.Meta, &stored); err != nil {
					t.Fatal(err)
				}
				if len(stored) != len(meta) {
					t.Fatal("metadata order/duplicates changed")
				}
				for i, entry := range stored {
					position := i
					if duplicates {
						position = i / 2
					}
					var id string
					if err := json.Unmarshal(entry["payment_category_id"], &id); err != nil || id != ids[position].String() || string(entry["extra"]) != fmt.Sprintf("900719925474%04d", position+993) {
						t.Fatalf("metadata %d changed: %s", i, entry)
					}
				}
				// Exercise the complete service enrichment as well as the two
				// repository operations. A stub method-call count cannot establish
				// the actual SQL bound or duplicate/value preservation end to end.
				trace.take()
				data, err := service.NewPaymentInfo(repo).Read(context.Background(), domain.ResolvedIdentity{}, paymentInfoFixtureID(226000).String(), "")
				serviceQueries := trace.take()
				if err != nil || len(serviceQueries) != wantQueries {
					t.Fatalf("complete service queries=%d want=%d error=%v SQL=%v", len(serviceQueries), wantQueries, err, serviceQueries)
				}
				for _, query := range serviceQueries {
					if !strings.HasPrefix(query, "SELECT") {
						t.Fatalf("service issued non-read statement: %s", query)
					}
				}
				info, ok := data["info"].(map[string]any)
				if !ok {
					t.Fatalf("service info: %T", data["info"])
				}
				enriched, ok := info["meta_payment_categories"].([]any)
				if !ok || len(enriched) != len(meta) {
					t.Fatalf("enriched metadata: %#v", info["meta_payment_categories"])
				}
				for i, value := range enriched {
					position := i
					if duplicates {
						position = i / 2
					}
					entry, ok := value.(map[string]any)
					if !ok || entry["payment_category_id"] != ids[position].String() || entry["payment_category_name"] != fmt.Sprintf("Batch %d", position) || entry["extra"] != json.Number(fmt.Sprintf("900719925474%04d", position+993)) || entry["amount"] != "0.01" {
						t.Fatalf("service changed metadata %d: %#v", i, value)
					}
				}
				t.Logf("%d distinct global/own/foreign/deleted categories, %d entries: repository %d / complete service %d actual SQL queries", count, len(meta), len(queries), len(serviceQueries))
			})
		}
	}
	// A batch may include duplicates and missing IDs; absence is data for the
	// service to validate, not a repository error or a per-entry fallback query.
	trace.take()
	names, err := repo.ReadPaymentInfoCategoryNames(context.Background(), []uuid.UUID{ids[0], ids[0], paymentInfoFixtureID(229999)})
	if err != nil || len(names) != 1 || names[ids[0]] != "Batch 0" || len(trace.take()) != 1 {
		t.Fatalf("mixed duplicate/missing batch: %v %v", names, err)
	}
	if err := tx.Rollback(); err != nil {
		t.Fatal(err)
	}
	if err := db.Get(&collisions, `SELECT count(*) FROM application_paymentcategory WHERE payment_category_id=ANY($1::uuid[])`, idStrings); err != nil || collisions != 0 {
		t.Fatalf("batch fixture leaked: %d %v", collisions, err)
	}
}

func TestPaymentInfoBlockedQueryCancellationLive(t *testing.T) {
	for _, table := range []string{"application_payment", "application_paymentcategory"} {
		t.Run(table, func(t *testing.T) {
			control := paymentInfoDB(t)
			trace := &paymentInfoQueryTrace{started: make(chan uint32, 1)}
			readerDB := paymentInfoTracedDB(t, trace)
			blocker, err := control.BeginTxx(context.Background(), nil)
			if err != nil {
				t.Fatal(err)
			}
			defer blocker.Rollback()
			// Both names are fixed above; this lock belongs solely to the private fixture.
			if _, err := blocker.Exec(`LOCK TABLE ` + table + ` IN ACCESS EXCLUSIVE MODE`); err != nil {
				t.Fatal(err)
			}
			var blockerPID int
			if err := blocker.Get(&blockerPID, `SELECT pg_backend_pid()`); err != nil {
				t.Fatal(err)
			}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			done := make(chan error, 1)
			go func() {
				repo := repository.NewPaymentInfo(readerDB)
				if table == "application_payment" {
					row, err := repo.ReadPaymentInfo(ctx, domain.PaymentInfoLookup{PaymentID: paymentInfoFixtureID(226000)})
					if row != nil {
						done <- fmt.Errorf("blocked canceled read returned data")
						return
					}
					done <- err
				} else {
					names, err := repo.ReadPaymentInfoCategoryNames(ctx, []uuid.UUID{paymentInfoFixtureID(225000)})
					if names != nil {
						done <- fmt.Errorf("blocked canceled category read returned data")
						return
					}
					done <- err
				}
			}()
			var readerPID uint32
			select {
			case readerPID = <-trace.started:
			case err := <-done:
				t.Fatalf("read finished without blocking: %v", err)
			case <-time.After(10 * time.Second):
				t.Fatal("reader did not reach PostgreSQL")
			}
			deadline := time.Now().Add(10 * time.Second)
			for {
				var blocked bool
				if err := control.Get(&blocked, `SELECT $1::int=ANY(pg_blocking_pids($2::int))`, blockerPID, readerPID); err != nil {
					t.Fatal(err)
				}
				if blocked {
					break
				}
				if time.Now().After(deadline) {
					t.Fatal("PostgreSQL never observed the expected blocker")
				}
				time.Sleep(10 * time.Millisecond)
			}
			cancel()
			select {
			case err := <-done:
				if !errors.Is(err, context.Canceled) {
					t.Fatalf("canceled read: %v", err)
				}
			case <-time.After(5 * time.Second):
				t.Fatal("blocked read did not cancel")
			}
			// Returning context.Canceled alone does not prove PostgreSQL stopped
			// the query. Keep the conflicting lock held until both the actual
			// backend and the client pool have released the canceled operation.
			releasedCtx, stopReleased := context.WithTimeout(context.Background(), 5*time.Second)
			defer stopReleased()
			tick := time.NewTicker(10 * time.Millisecond)
			defer tick.Stop()
			for {
				var state struct {
					Idle, Unblocked, BlockerHeld bool
				}
				if err := control.GetContext(releasedCtx, &state, `SELECT
 NOT EXISTS (SELECT 1 FROM pg_stat_activity WHERE pid=$1
  AND (state IS DISTINCT FROM 'idle' OR xact_start IS NOT NULL)) AS idle,
 cardinality(pg_blocking_pids($1::int))=0 AS unblocked,
 EXISTS (SELECT 1 FROM pg_locks WHERE pid=$2 AND relation=$3::regclass
  AND mode='AccessExclusiveLock' AND granted) AS blockerheld`, readerPID, blockerPID, table); err != nil {
					t.Fatalf("observe canceled backend before releasing blocker: %v", err)
				}
				if !state.BlockerHeld {
					t.Fatal("blocker lock disappeared before canceled backend cleanup was proved")
				}
				inUse := readerDB.Stats().InUse
				if state.Idle && state.Unblocked && inUse == 0 {
					break
				}
				select {
				case <-releasedCtx.Done():
					t.Fatalf("canceled backend not released while blocker held: idle=%t unblocked=%t poolInUse=%d", state.Idle, state.Unblocked, inUse)
				case <-tick.C:
				}
			}
			t.Logf("observed PostgreSQL blocker %d -> reader %d; reader idle/gone with no transaction/wait and pool InUse=0 before blocker release", blockerPID, readerPID)
			if err := blocker.Rollback(); err != nil {
				t.Fatal(err)
			}
			if row := paymentInfoRead(t, repository.NewPaymentInfo(readerDB), 226000, nil, nil); row == nil {
				t.Fatal("pool not usable after cancellation")
			}
		})
	}
}
