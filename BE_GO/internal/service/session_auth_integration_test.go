//go:build sessionauthintegration

package service

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

type sessionAuthQueries struct{ count atomic.Int64 }

func (q *sessionAuthQueries) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	if strings.HasPrefix(strings.ToUpper(strings.TrimSpace(d.SQL)), "SELECT") {
		q.count.Add(1)
	}
	return ctx
}
func (*sessionAuthQueries) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

type sessionCacheCommands struct{ reads, deletes, oversized atomic.Int64 }

func (*sessionCacheCommands) DialHook(next redis.DialHook) redis.DialHook { return next }
func (*sessionCacheCommands) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return next
}
func (h *sessionCacheCommands) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		if cmd.Name() == "getrange" {
			h.reads.Add(1)
		}
		if cmd.Name() == "del" {
			h.deletes.Add(1)
		}
		err := next(ctx, cmd)
		if cmd.Name() == "getrange" {
			if result, ok := cmd.(*redis.StringCmd); ok && len(result.Val()) > (1<<20)+1 {
				h.oversized.Add(1)
			}
		}
		return err
	}
}

func TestDjangoSessionAuthentication(t *testing.T) {
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_SESSION_AUTH_FIXTURE"))
	if err != nil {
		t.Fatal("run accounts.py --only session-auth")
	}
	var fixture struct {
		Secret, Address string
		TransitionKey   string `json:"transition_key"`
		Cases           []struct {
			Name, Key            string
			CacheKey             string `json:"cache_key"`
			ActorID              string `json:"actor_id"`
			ActorEmail           string `json:"actor_email"`
			Empty, Error, Delete bool
			Queries              int64
			Before               *string
		}
	}
	if json.Unmarshal(raw, &fixture) != nil || len(fixture.Cases) != 43 {
		t.Fatal("invalid session fixture")
	}
	cfg, err := pgx.ParseConfig(os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN"))
	if err != nil {
		t.Fatal("invalid private database config")
	}
	queries := &sessionAuthQueries{}
	cfg.Tracer = queries
	db := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	db.SetMaxOpenConns(4)
	defer db.Close()
	client := redis.NewClient(&redis.Options{Addr: fixture.Address, Password: "synthetic-identity-only", DB: 1, MaxRetries: -1, ContextTimeoutEnabled: true, PoolSize: 4})
	defer client.Close()
	hook := &sessionCacheCommands{}
	client.AddHook(hook)
	cache, err := repository.NewDjangoSessionCache(client)
	if err != nil {
		t.Fatal(err)
	}
	s := NewSessionAuthentication(cache, repository.NewSessionAccounts(db), fixture.Secret)
	ctx := context.Background()
	for _, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			beforeTTL := client.PTTL(ctx, tc.CacheKey).Val()
			queries.count.Store(0)
			reads, deletes := hook.reads.Load(), hook.deletes.Load()
			got, err := s.Resolve(ctx, tc.Key)
			wantReads, wantDeletes := int64(1), int64(0)
			if utf8.RuneCountInString(tc.Key) < 8 {
				wantReads = 0
			}
			if tc.Delete {
				wantDeletes = 1
			}
			if hook.reads.Load()-reads != wantReads || hook.deletes.Load()-deletes != wantDeletes {
				t.Fatal("unbounded session cache commands")
			}
			if (err != nil) != tc.Error {
				t.Fatal("session error boundary differs")
			}
			if !tc.Error {
				id, email := "", ""
				if got.Actor != nil {
					id = got.Actor.ID
					email = got.Actor.Email
				}
				if id != tc.ActorID || email != tc.ActorEmail || got.Empty != tc.Empty {
					t.Fatal("session actor/empty state differs")
				}
			}
			if queries.count.Load() != tc.Queries || db.Stats().InUse != 0 {
				t.Fatal("query/pool bound differs")
			}
			current, readErr := client.Get(ctx, tc.CacheKey).Bytes()
			if tc.Before == nil || tc.Delete {
				if !errors.Is(readErr, redis.Nil) {
					t.Fatal("missing/deleted session still exists")
				}
			} else {
				want, decodeErr := base64.StdEncoding.DecodeString(*tc.Before)
				if decodeErr != nil || readErr != nil || !bytes.Equal(current, want) {
					t.Fatal("session payload changed")
				}
				if ttl := client.PTTL(ctx, tc.CacheKey).Val(); ttl <= 0 || ttl > beforeTTL {
					t.Fatal("session read renewed TTL")
				}
			}
		})
	}
	valid := fixture.Cases[5]
	queries.count.Store(0)
	concurrentReads := hook.reads.Load()
	var group sync.WaitGroup
	for range 32 {
		group.Add(1)
		go func() {
			defer group.Done()
			got, err := s.Resolve(ctx, valid.Key)
			if err != nil || got.Actor == nil || got.Actor.ID != valid.ActorID {
				t.Error("concurrent session identity differs")
			}
		}()
	}
	group.Wait()
	if queries.count.Load() != 32 || db.Stats().InUse != 0 || db.Stats().OpenConnections > 4 || hook.reads.Load()-concurrentReads != 32 || client.PoolStats().TotalConns > 4 {
		t.Fatal("concurrent session query/pool bound differs")
	}
	t.Run("current account transitions revoke cached session", func(t *testing.T) {
		var original struct {
			Email, Password string
			Active          bool `db:"is_active"`
			Deleted         bool
		}
		if err := db.GetContext(ctx, &original, `SELECT email,password,is_active,deleted FROM bakney_user WHERE user_id=$1`, valid.ActorID); err != nil {
			t.Fatal("fixture account unavailable")
		}
		t.Cleanup(func() {
			if _, err := db.ExecContext(ctx, `UPDATE bakney_user SET email=$2,password=$3,is_active=$4,deleted=$5 WHERE user_id=$1`, valid.ActorID, original.Email, original.Password, original.Active, original.Deleted); err != nil {
				t.Error("fixture account restore failed")
			}
		})
		check := func(email string, empty bool) {
			t.Helper()
			queries.count.Store(0)
			got, err := s.Resolve(ctx, fixture.TransitionKey)
			if err != nil || got.Empty != empty || queries.count.Load() != 1 || db.Stats().InUse != 0 {
				t.Fatal("state transition/query bound differs")
			}
			if email == "" {
				if got.Actor != nil {
					t.Fatal("revoked/inactive account retained actor")
				}
			} else if got.Actor == nil || got.Actor.ID != valid.ActorID || got.Actor.Email != email {
				t.Fatal("current actor state not observed")
			}
		}
		update := func(query string, args ...any) {
			t.Helper()
			if _, err := db.ExecContext(ctx, query, args...); err != nil {
				t.Fatal("fixture transition failed")
			}
		}
		check(original.Email, false)
		update(`UPDATE bakney_user SET email=$2 WHERE user_id=$1`, valid.ActorID, "session-changed@example.test")
		check("session-changed@example.test", false)
		update(`UPDATE bakney_user SET is_active=false WHERE user_id=$1`, valid.ActorID)
		check("", false)
		update(`UPDATE bakney_user SET is_active=true,deleted=true WHERE user_id=$1`, valid.ActorID)
		check("session-changed@example.test", false)
		update(`UPDATE bakney_user SET password=$2 WHERE user_id=$1`, valid.ActorID, "!synthetic-session-password-change")
		check("", true)
		if n, err := client.Exists(ctx, ":1:django.contrib.sessions.cache"+fixture.TransitionKey).Result(); err != nil || n != 0 {
			t.Fatal("password change did not remove Django session")
		}
	})
	t.Run("bounded cache response and deadline recovery", func(t *testing.T) {
		key := "session-auth-resource-fixture"
		cacheKey := ":1:django.contrib.sessions.cache" + key
		defer client.Del(ctx, cacheKey)
		if err := client.Set(ctx, cacheKey, strings.Repeat("x", 2<<20), time.Minute).Err(); err != nil {
			t.Fatal("cannot seed resource fixture")
		}
		queries.count.Store(0)
		reads := hook.reads.Load()
		got, err := s.Resolve(ctx, key)
		if err != nil || got.Actor != nil || !got.Empty || queries.count.Load() != 0 || hook.reads.Load()-reads != 1 || hook.oversized.Load() != 0 {
			t.Fatal("oversized cache response not bounded")
		}
		if n := client.StrLen(ctx, cacheKey).Val(); n != 2<<20 {
			t.Fatal("invalid cache load mutated session")
		}
		if err := client.Do(ctx, "CLIENT", "PAUSE", 200, "ALL").Err(); err != nil {
			t.Fatal("cannot pause private Redis")
		}
		deadline, cancel := context.WithTimeout(ctx, 25*time.Millisecond)
		defer cancel()
		reads = hook.reads.Load()
		got, err = s.Resolve(deadline, valid.Key)
		// A transport deadline can fire just before Context.Err becomes visible.
		// It must still never admit an actor, retry, or reach PostgreSQL.
		if got.Actor != nil || (err != nil && !errors.Is(err, context.DeadlineExceeded)) || queries.count.Load() != 0 || hook.reads.Load()-reads != 1 {
			t.Fatal("paused cache exceeded cancellation boundary")
		}
		if err == nil && !got.Empty {
			t.Fatal("transport failure retained a nonempty session")
		}
		time.Sleep(250 * time.Millisecond)
		got, err = s.Resolve(ctx, valid.Key)
		if err != nil || got.Actor == nil || got.Actor.ID != valid.ActorID || queries.count.Load() != 1 || client.PoolStats().TotalConns > 4 || db.Stats().InUse != 0 {
			t.Fatal("cache pool did not recover after deadline")
		}
	})
	cancelled, cancel := context.WithCancel(ctx)
	cancel()
	queries.count.Store(0)
	if _, err := s.Resolve(cancelled, valid.Key); !errors.Is(err, context.Canceled) || queries.count.Load() != 0 {
		t.Fatal("cancelled identity queried")
	}
	db.Close()
	if got, err := s.Resolve(ctx, valid.Key); err == nil || got.Actor != nil {
		t.Fatal("database failure admitted an actor")
	}
	client.Close()
	if got, err := s.Resolve(ctx, valid.Key); err != nil || got.Actor != nil || !got.Empty {
		t.Fatal("cache load failure did not match Django anonymous reset")
	}
}
