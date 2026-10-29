//go:build cacheinterop

package repository

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/redis/go-redis/v9"
)

func TestImpersonationCacheDjangoInteroperability(t *testing.T) {
	address := os.Getenv("ASSOZETA_IMPERSONATION_CACHE")
	if address == "" {
		t.Fatal("run through TESTS/adapters/django/impersonation_cache.py")
	}
	client := redis.NewClient(&redis.Options{Addr: address, DB: 1, Password: "synthetic-cache-only", ContextTimeoutEnabled: true})
	defer client.Close()
	store, err := NewDjangoImpersonationCache(client)
	if err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	uid := func(n int) string { return fmt.Sprintf("00000000-0000-4000-8000-%012d", n) }
	admin := domain.ImpersonationSession{ActorID: uid(1), TargetID: uid(2)}
	owner := admin
	owner.AssociationID = uid(3)
	for id, want := range map[string]domain.ImpersonationSession{uid(10): admin, uid(11): owner} {
		key, _ := impersonationCacheKey(id)
		before := client.PTTL(ctx, key).Val()
		got, err := store.Get(ctx, id)
		if err != nil || got != want {
			t.Fatalf("Django session read: %v", err)
		}
		after := client.PTTL(ctx, key).Val()
		if before <= 0 || after > before || before > ImpersonationTTL {
			t.Fatal("read renewed TTL or lost expiry")
		}
	}
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_IMPERSONATION_FIXTURE"))
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Vectors []struct {
			ID     string
			Record domain.ImpersonationSession
		}
	}
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	if len(fixture.Vectors) != 8 {
		t.Fatal("missing cross-version fixtures")
	}
	for _, vector := range fixture.Vectors {
		got, err := store.Get(ctx, vector.ID)
		if err != nil || got != vector.Record {
			t.Fatalf("protocol fixture %s: %v", vector.ID, err)
		}
	}
	// Header aliases resolve the same canonical UUID cache key.
	for _, id := range []string{strings.ReplaceAll(uid(10), "-", ""), "urn:uuid:" + uid(10), "{" + uid(10) + "}"} {
		if got, err := store.Get(ctx, id); err != nil || got != admin {
			t.Fatal("valid UUID alias failed")
		}
	}
	for _, id := range []string{uid(20), uid(999)} {
		if _, err := store.Get(ctx, id); !errors.Is(err, domain.ErrImpersonationNotFound) {
			t.Fatalf("revoked/missing session: %v", err)
		}
	}
	deadline := time.Now().Add(3 * time.Second)
	for {
		_, err := store.Get(ctx, uid(21))
		if errors.Is(err, domain.ErrImpersonationNotFound) {
			break
		}
		if err != nil || time.Now().After(deadline) {
			t.Fatalf("session did not expire: %v", err)
		}
		time.Sleep(25 * time.Millisecond)
	}
	if _, err := store.Get(ctx, "invalid"); !errors.Is(err, domain.ErrInvalidImpersonation) {
		t.Fatal("invalid session identifier accepted")
	}
	if err := store.Put(ctx, uid(30), admin); err != nil {
		t.Fatal(err)
	}
	if err := store.Put(ctx, uid(31), owner); err != nil {
		t.Fatal(err)
	}
	if err := store.Delete(ctx, uid(11)); err != nil {
		t.Fatal(err)
	}
	if err := store.Delete(ctx, uid(999)); err != nil {
		t.Fatal("idempotent missing-session delete failed")
	}
	// No local authorization caching: concurrent readers must observe revocation
	// on subsequent requests. No assertion about already-admitted requests is made.
	if err := store.Put(ctx, uid(40), admin); err != nil {
		t.Fatal(err)
	}
	var readers sync.WaitGroup
	for i := 0; i < 16; i++ {
		readers.Add(1)
		go func() {
			defer readers.Done()
			if got, err := store.Get(ctx, uid(40)); err != nil || got != admin {
				t.Error("concurrent session read failed")
			}
		}()
	}
	readers.Wait()
	if err := store.Delete(ctx, uid(40)); err != nil {
		t.Fatal(err)
	}
	if _, err := store.Get(ctx, uid(40)); !errors.Is(err, domain.ErrImpersonationNotFound) {
		t.Fatal("revocation was cached away")
	}
	cancelled, cancel := context.WithCancel(ctx)
	cancel()
	if _, err := store.Get(cancelled, uid(30)); !errors.Is(err, context.Canceled) {
		t.Fatalf("cache cancellation: %v", err)
	}
}
