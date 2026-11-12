//go:build twofactorsetupintegration

package service

import (
	"context"
	"crypto/rand"
	"fmt"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/qrcode"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

type twoFactorSetupQueries struct{ count atomic.Int64 }

func (q *twoFactorSetupQueries) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	if strings.HasPrefix(strings.ToLower(strings.TrimSpace(d.SQL)), "select") {
		q.count.Add(1)
	}
	return ctx
}
func (*twoFactorSetupQueries) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

func TestTwoFactorSetupPostgres(t *testing.T) {
	if os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN") == "" || os.Getenv("ASSOZETA_IDENTITY_CACHE") == "" {
		t.Fatal("run accounts.py --only two-factor-setup")
	}
	cfg, err := pgx.ParseConfig(os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN"))
	if err != nil {
		t.Fatal("invalid private database configuration")
	}
	queries := &twoFactorSetupQueries{}
	cfg.Tracer = queries
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	db := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(4)
	db.SetMaxIdleConns(4)
	cache := redis.NewClient(&redis.Options{Addr: os.Getenv("ASSOZETA_IDENTITY_CACHE"), DB: 1, Password: "synthetic-identity-only", MaxRetries: -1, ContextTimeoutEnabled: true})
	defer cache.Close()
	sessions, err := repository.NewDjangoImpersonationCache(cache)
	if err != nil {
		t.Fatal(err)
	}
	accounts := repository.NewAccounts(db)
	relations := repository.NewIdentityRelations(db)
	resolver := NewIdentityResolver(accounts, relations, sessions, NewPermissions(), NewScopedRequestGuard(repository.NewScopedResources(db), relations, sessions))
	flow := NewTwoFactorSetup(rand.Reader, qrcode.New(), "support@example.test", "Contract")
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	resolve := func(id int) (domain.ResolvedIdentity, error) {
		account, err := accounts.ByID(ctx, fmt.Sprintf("00000000-0000-4000-8000-%012d", id))
		if err != nil {
			return domain.ResolvedIdentity{}, err
		}
		return resolver.Resolve(ctx, &account, domain.IdentityRequest{Path: "/two-fa/setup", Method: "GET", References: &domain.RequestReferences{Operation: "two_fa_setup"}})
	}
	cases := []struct {
		id      int
		queries int64
	}{{8, 1}, {10, 1}, {81600, 2}, {14, 2}}
	for _, tc := range cases {
		queries.count.Store(0)
		identity, err := resolve(tc.id)
		if err != nil {
			t.Fatal("fixture admission failed", err)
		}
		before := queries.count.Load()
		value, err := flow.Setup(ctx, identity)
		if err != nil || len(value.Secret) != 32 || !strings.HasPrefix(value.QRCodeURI, "data:image/svg+xml;charset=utf-8,") || before != tc.queries || queries.count.Load() != before {
			t.Fatal("setup or admission/provisioning query budget differs", err)
		}
	}
	queries.count.Store(0)
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			tc := cases[i%len(cases)]
			identity, err := resolve(tc.id)
			if err != nil {
				t.Error("concurrent scope admission failed", err)
				return
			}
			value, err := flow.Setup(ctx, identity)
			if err != nil || len(value.Secret) != 32 || !strings.HasPrefix(value.QRCodeURI, "data:image/svg+xml;charset=utf-8,") {
				t.Error("concurrent provisioning failed", err)
			}
		}(i)
	}
	wg.Wait()
	if queries.count.Load() != 48 || db.Stats().InUse != 0 || db.Stats().OpenConnections > 4 {
		t.Fatal("concurrent setup exceeded query or connection budget")
	}
}
