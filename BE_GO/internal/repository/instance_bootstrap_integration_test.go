//go:build instancebootstrapintegration

package repository

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

type instanceQueryTrace struct {
	mu      sync.Mutex
	queries []string
}

func (q *instanceQueryTrace) TraceQueryStart(ctx context.Context, _ *pgx.Conn, data pgx.TraceQueryStartData) context.Context {
	q.mu.Lock()
	q.queries = append(q.queries, data.SQL)
	q.mu.Unlock()
	return ctx
}
func (*instanceQueryTrace) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}
func (q *instanceQueryTrace) reset()                                                        { q.mu.Lock(); q.queries = nil; q.mu.Unlock() }
func (q *instanceQueryTrace) selects() int {
	q.mu.Lock()
	defer q.mu.Unlock()
	count := 0
	for _, query := range q.queries {
		if strings.HasPrefix(strings.TrimSpace(query), "SELECT") {
			count++
		}
	}
	return count
}

func TestInstanceBootstrapPostgresBounds(t *testing.T) {
	cfg, err := pgx.ParseConfig(os.Getenv("ASSOZETA_INSTANCE_BOOTSTRAP_DSN"))
	if err != nil || cfg.Database != "assozeta_contract_bootstrap" || cfg.User != "contracts" || !strings.HasPrefix(filepath.Base(cfg.Host), "az-bootstrap-") {
		t.Fatal("private current-Django fixture required")
	}
	trace := &instanceQueryTrace{}
	cfg.Tracer = trace
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	db := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(2)
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	exec := func(query string, args ...any) {
		t.Helper()
		if _, err := db.ExecContext(ctx, query, args...); err != nil {
			t.Fatal(err)
		}
	}
	var firstID int64
	if err := db.GetContext(ctx, &firstID, "SELECT min(id) FROM instance_instanceconfiguration"); err != nil {
		t.Fatal(err)
	}
	exec(`UPDATE instance_instanceconfiguration SET primary_association_id='00000000-0000-4000-8000-000000820001',
 integration_settings='{"ai":{"settings":{"enabled":true,"api_key":"private-api-canary"}},"stripe":{"settings":{"enabled":true,"public_key":"pk_public","secret_key":"private-stripe-canary"}}}'::jsonb WHERE id=$1`, firstID)
	if !t.Run("pair array secrets projected before snapshot", func(t *testing.T) {
		exec(`UPDATE instance_instanceconfiguration SET integration_settings='{"ai":{"settings":[["enabled",true],["api_key","private-array-canary"]]}}'::jsonb WHERE id=$1`, firstID)
		value, err := NewInstanceBootstrap(db).FirstInstance(ctx)
		if err != nil || value == nil {
			t.Fatal(err)
		}
		if strings.Contains(string(value.PublicIntegrations), "private-array-canary") {
			t.Fatal("accepted pair array selected encrypted credential into public snapshot")
		}
	}) {
		return
	}
	exec(`UPDATE instance_instanceconfiguration SET integration_settings='{"ai":{"settings":{"enabled":true,"api_key":"private-api-canary"}},"stripe":{"settings":{"enabled":true,"public_key":"pk_public","secret_key":"private-stripe-canary"}}}'::jsonb WHERE id=$1`, firstID)
	reports := map[string]json.RawMessage{}
	prior := 1
	for _, size := range []int{100, 1000, 10000} {
		t.Run(fmt.Sprintf("rows%d", size), func(t *testing.T) {
			// Clone fully populated current-model rows; only trusted fixture identifiers
			// change. This deliberately bypasses singleton save(), as historical bulk
			// rows can. It does not propose supported multiple-instance administration.
			exec(`INSERT INTO bakney_user SELECT (jsonb_populate_record(NULL::bakney_user,to_jsonb(u)||jsonb_build_object(
    'user_id',('10000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid,'username','BOUND-'||n,'email','bound-'||n||'@example.test'))).*
    FROM bakney_user u CROSS JOIN generate_series($1::int,$2::int) n WHERE u.user_id='00000000-0000-4000-8000-000000810000'`, prior+1, size)
			exec(`INSERT INTO application_sportassociation SELECT (jsonb_populate_record(NULL::application_sportassociation,to_jsonb(a)||jsonb_build_object(
    'sport_association_id',('20000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid,'user_id',('10000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid))).*
    FROM application_sportassociation a CROSS JOIN generate_series($1::int,$2::int) n WHERE a.sport_association_id='00000000-0000-4000-8000-000000820001'`, prior+1, size)
			exec(`INSERT INTO instance_instanceconfiguration SELECT (jsonb_populate_record(NULL::instance_instanceconfiguration,to_jsonb(c)||jsonb_build_object(
    'id',$3::bigint+n,'domain','bound-'||n||'.example.test','name','Later row','primary_association_id',('20000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid))).*
    FROM instance_instanceconfiguration c CROSS JOIN generate_series($1::int,$2::int) n WHERE c.id=$3`, prior+1, size, firstID)
			exec("ANALYZE instance_instanceconfiguration")
			exec("ANALYZE application_sportassociation")
			trace.reset()
			value, err := NewInstanceBootstrap(db).FirstInstance(ctx)
			if err != nil || value == nil || value.Name != "Fixture à ASD" || value.AssociationID == nil || *value.AssociationID != "00000000-0000-4000-8000-000000820001" {
				t.Fatal("first PK projection changed", err)
			}
			if trace.selects() != 1 {
				t.Fatal("projection query count", trace.selects())
			}
			if strings.Contains(string(value.PublicIntegrations), "private-") {
				t.Fatal("encrypted integration field selected")
			}
			var raw []byte
			if err := db.GetContext(ctx, &raw, "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) "+instanceBootstrapSQL); err != nil {
				t.Fatal(err)
			}
			var plans []map[string]any
			if err := json.Unmarshal(raw, &plans); err != nil {
				t.Fatal(err)
			}
			root := plans[0]["Plan"].(map[string]any)
			if root["Actual Rows"] != float64(1) {
				t.Fatal("result row bound")
			}
			var inspect func(map[string]any)
			inspect = func(plan map[string]any) {
				if size >= 1000 && plan["Node Type"] == "Seq Scan" && (plan["Relation Name"] == "instance_instanceconfiguration" || plan["Relation Name"] == "application_sportassociation") {
					t.Fatal("large fixture scanned relation instead of PK seek", plan["Relation Name"])
				}
				if children, ok := plan["Plans"].([]any); ok {
					for _, child := range children {
						inspect(child.(map[string]any))
					}
				}
			}
			inspect(root)
			if size >= 1000 && root["Shared Hit Blocks"].(float64) > 64 {
				t.Fatal("unexpected projection buffer cost", root["Shared Hit Blocks"])
			}
			reports[fmt.Sprint(size)] = raw
			prior = size
		})
	}
	data, err := json.MarshalIndent(reports, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	destination := os.Getenv("ASSOZETA_INSTANCE_BOOTSTRAP_PLANS")
	if destination == "" {
		t.Fatal("explicit private plan receipt path required")
	}
	if err := os.WriteFile(destination, append(data, '\n'), 0600); err != nil {
		t.Fatal(err)
	}
}
