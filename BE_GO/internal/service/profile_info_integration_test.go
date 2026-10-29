//go:build profileinfointegration

package service

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	ownedpgx "github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/loginimage"
	"github.com/jackc/pgx/v5"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

type profileInfoQueries struct {
	count   atomic.Int64
	capture atomic.Bool
	mu      sync.Mutex
	sql     string
	args    []any
}

func (q *profileInfoQueries) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	if strings.HasPrefix(strings.ToLower(strings.TrimSpace(d.SQL)), "select") {
		q.count.Add(1)
		if q.capture.Load() {
			q.mu.Lock()
			q.sql = d.SQL
			q.args = append([]any(nil), d.Args...)
			q.mu.Unlock()
		}
	}
	return ctx
}
func (*profileInfoQueries) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}
func TestProfileInfoPostgres(t *testing.T) {
	dsn, path, address := os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN"), os.Getenv("TESTS_PROFILE_INFO_FIXTURE"), os.Getenv("ASSOZETA_IDENTITY_CACHE")
	if dsn == "" || path == "" || address == "" {
		t.Fatal("run accounts.py --only profile-info")
	}
	cfg, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal("invalid private database configuration")
	}
	queries := &profileInfoQueries{}
	cfg.Tracer = queries
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	db := sqlx.NewDb(ownedpgx.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(4)
	db.SetMaxIdleConns(4)
	cache := redis.NewClient(&redis.Options{Addr: address, DB: 1, Password: "synthetic-identity-only", MaxRetries: -1, ContextTimeoutEnabled: true})
	defer cache.Close()
	sessions, err := repository.NewDjangoImpersonationCache(cache)
	if err != nil {
		t.Fatal(err)
	}
	accounts := repository.NewAccounts(db)
	relations := repository.NewIdentityRelations(db)
	resolver := NewIdentityResolver(accounts, relations, sessions, NewPermissions(), NewScopedRequestGuard(repository.NewScopedResources(db), relations, sessions))
	images, err := loginimage.NewProcessor(os.Getenv("ASSOZETA_IMAGE_CODEC"), loginimage.ProcessorLimits{Codec: loginimage.JPEGLimits{MaxRasterBytes: 64 << 20, MaxEncodedBytes: 8 << 20, Concurrency: 2, Timeout: 10 * time.Second}, MaxURIBytes: 16 << 20, MaxHeaderBytes: 8 << 20, MaxTailBytes: 8 << 20, MaxDecodeBytes: 64 << 20, MaxTransformBytes: 128 << 20})
	if err != nil {
		t.Fatal(err)
	}
	details := repository.NewProfileInfoDetails(db)
	flow := NewProfileInfo(NewLoginProfiles(repository.NewLoginProfiles(db), time.Now), NewAssociationProfiles(repository.NewAssociationProfiles(db), time.Now), details, images, domain.RegistrationChoices{Family: true})
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	id := func(n int) string { return fmt.Sprintf("00000000-0000-4000-8000-%012d", n) }
	resolve := func(n int) (domain.ResolvedIdentity, error) {
		account, err := accounts.ByID(ctx, id(n))
		if err != nil {
			return domain.ResolvedIdentity{}, err
		}
		return resolver.Resolve(ctx, &account, domain.IdentityRequest{Path: "/profile/info", Method: "GET", References: &domain.RequestReferences{Operation: "profile_info"}})
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Cases []struct {
			Name     string
			Response json.RawMessage
		}
	}
	if err = json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	expected := map[string]json.RawMessage{}
	for _, c := range fixture.Cases {
		expected[c.Name] = c.Response
	}
	comparable := func(data []byte) any {
		var value any
		decoder := json.NewDecoder(bytes.NewReader(data))
		decoder.UseNumber()
		if err := decoder.Decode(&value); err != nil {
			t.Error(err)
		}
		return value
	}
	cases := []struct {
		name              string
		n                 int
		queries, protocol int64
	}{
		// Profiles always open one snapshot; owner/collaborator also open the
		// association snapshot. The direct detail and identity reads are unchanged.
		{"owner", 82000, 6, 2}, {"athlete", 82002, 2, 1},
		{"collaborator", 82003, 6, 2}, {"administrator", 82004, 2, 1},
	}
	for _, c := range cases {
		queries.count.Store(0)
		identity, err := resolve(c.n)
		if err != nil {
			t.Fatal(err)
		}
		value, err := flow.Read(ctx, identity)
		if err != nil {
			t.Fatal(err)
		}
		actual, err := json.Marshal(value)
		if err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(comparable(actual), comparable(expected[c.name])) {
			t.Fatal(c.name, "profile differs")
		}
		if got := queries.count.Load(); got != c.queries+c.protocol {
			t.Fatal(c.name, "SELECT budget", got, c.queries+c.protocol)
		}
	}
	queries.count.Store(0)
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			identity, err := resolve(cases[i%4].n)
			if err != nil {
				t.Error(err)
				return
			}
			if _, err = flow.Read(ctx, identity); err != nil {
				t.Error(err)
			}
		}(i)
	}
	wg.Wait()
	if queries.count.Load() != 128+8*(2+1+2+1) || db.Stats().InUse != 0 || db.Stats().OpenConnections > 4 {
		t.Fatal("concurrent query/pool budget", queries.count.Load(), db.Stats())
	}
	// Grow one tenant's nested groups while foreign rows remain present.
	remove := func() {
		if _, err := db.ExecContext(context.Background(), `DELETE FROM application_group WHERE sport_association_id=$1 AND group_id>=$2 AND group_id<$3`, id(83000), id(86000), id(87000)); err != nil {
			t.Error(err)
		}
	}
	defer remove()
	for _, count := range []int{1, 16, 128, 512} {
		remove()
		_, err = db.ExecContext(ctx, `INSERT INTO application_group(group_id,name,description,sport_association_id)
   SELECT ('00000000-0000-4000-8000-'||lpad((86000+i)::text,12,'0'))::uuid,'Nested '||i,'fixture',$1 FROM generate_series(0,$2::int-1) i`, id(83000), count)
		if err != nil {
			t.Fatal(err)
		}
		queries.count.Store(0)
		identity, err := resolve(82000)
		if err != nil {
			t.Fatal(err)
		}
		value, err := flow.Read(ctx, identity)
		if err != nil {
			t.Fatal(err)
		}
		if len(value.UserData.SportAssociation.Groups) != count+3 || queries.count.Load() != 6+2 {
			t.Fatal("nested groups changed SELECT budget", count, queries.count.Load())
		}
		for _, group := range value.UserData.SportAssociation.Groups {
			if group.Value == id(84999) {
				t.Fatal("foreign group leaked")
			}
		}
	}
	remove()
	// Inspect the actual details statement over 4,000 additional synthetic tenants.
	var collision int
	if err = db.GetContext(ctx, &collision, `SELECT count(*) FROM bakney_user WHERE user_id::text LIKE '10000000-0000-4000-8000-%'`); err != nil || collision != 0 {
		t.Fatal("query-plan fixture namespace collides", err)
	}
	cleanupPlan := func() {
		if _, err := db.ExecContext(context.Background(), `DELETE FROM application_sportassociation WHERE sport_association_id::text LIKE '20000000-0000-4000-8000-%'`); err != nil {
			t.Error(err)
		}
		if _, err := db.ExecContext(context.Background(), `DELETE FROM bakney_user WHERE user_id::text LIKE '10000000-0000-4000-8000-%'`); err != nil {
			t.Error(err)
		}
	}
	defer cleanupPlan()
	if _, err = db.ExecContext(ctx, `WITH cloned AS MATERIALIZED (SELECT jsonb_populate_record(NULL::bakney_user,to_jsonb(u)||jsonb_build_object(
 'user_id','10000000-0000-4000-8000-'||lpad(i::text,12,'0'),'username','PROFILE-PLAN-'||i,'email','profile-plan-'||i||'@example.test')) AS row_data FROM bakney_user u CROSS JOIN generate_series(1,4000) i WHERE u.user_id=$1) INSERT INTO bakney_user SELECT (row_data).* FROM cloned`, id(82000)); err != nil {
		t.Fatal(err)
	}
	if _, err = db.ExecContext(ctx, `WITH cloned AS MATERIALIZED (SELECT jsonb_populate_record(NULL::application_sportassociation,to_jsonb(a)||jsonb_build_object(
 'sport_association_id','20000000-0000-4000-8000-'||lpad(i::text,12,'0'),'user_id','10000000-0000-4000-8000-'||lpad(i::text,12,'0'))) AS row_data FROM application_sportassociation a CROSS JOIN generate_series(1,4000) i WHERE a.sport_association_id=$1) INSERT INTO application_sportassociation SELECT (row_data).* FROM cloned`, id(83000)); err != nil {
		t.Fatal(err)
	}
	if _, err = db.ExecContext(ctx, `ANALYZE bakney_user,application_sportassociation`); err != nil {
		t.Fatal(err)
	}
	queries.capture.Store(true)
	if _, err = details.Read(ctx, id(82003), id(82000)); err != nil {
		t.Fatal(err)
	}
	queries.capture.Store(false)
	queries.mu.Lock()
	statement, args := queries.sql, append([]any(nil), queries.args...)
	queries.mu.Unlock()
	if statement == "" {
		t.Fatal("actual details statement not captured")
	}
	var plan []byte
	if err = db.GetContext(ctx, &plan, "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "+statement, args...); err != nil {
		t.Fatal(err)
	}
	var explained []struct {
		Plan struct {
			ActualRows int `json:"Actual Rows"`
		}
	}
	if err = json.Unmarshal(plan, &explained); err != nil || len(explained) != 1 || explained[0].Plan.ActualRows != 1 {
		t.Fatal("details query plan did not return exactly one profile", err)
	}
	output := os.Getenv("ASSOZETA_PROFILE_PLAN_OUTPUT")
	if output == "" {
		t.Fatal("private query-plan output required")
	}
	if err = os.WriteFile(output, plan, 0600); err != nil {
		t.Fatal(err)
	}
	cleanupPlan()
	if _, err = db.ExecContext(ctx, `ANALYZE bakney_user,application_sportassociation`); err != nil {
		t.Fatal(err)
	}
	if _, err := details.Read(ctx, id(82003), id(82001)); !errors.Is(err, domain.ErrAssociationNotFound) {
		t.Fatal("foreign owner accepted", err)
	}
	blocker, err := db.BeginTxx(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer blocker.Rollback()
	if _, err = blocker.ExecContext(ctx, `LOCK TABLE application_sportassociation IN ACCESS EXCLUSIVE MODE`); err != nil {
		t.Fatal(err)
	}
	blocked, stop := context.WithTimeout(ctx, 50*time.Millisecond)
	_, err = details.Read(blocked, id(82003), id(82000))
	stop()
	if err == nil || blocked.Err() == nil {
		t.Fatal("blocked read ignored cancellation")
	}
	if err = blocker.Rollback(); err != nil {
		t.Fatal(err)
	}
	if _, err = details.Read(ctx, id(82003), id(82000)); err != nil {
		t.Fatal("read did not recover", err)
	}
	if db.Stats().InUse != 0 {
		t.Fatal("connection leaked after cancellation")
	}
}
