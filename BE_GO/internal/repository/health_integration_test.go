//go:build readinessintegration

package repository

import (
	"context"
	"crypto/rand"
	"crypto/sha1"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// As with login_rate_integration_test.go, the parent supplies a JSON fixture
// for a disposable, authenticated Redis, not a service started by this test.
// Required: {"address":"127.0.0.1:PORT","password":"...","db":1}.
// Optional username defaults to Redis's default user. Redis 7+ and ACL admin
// access are required; missing fixture/capabilities fail instead of skipping.
func readinessIntegrationClient(t *testing.T) (context.Context, *redis.Client) {
	t.Helper()
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_READINESS_FIXTURE"))
	if err != nil {
		t.Fatal("provide ASSOZETA_READINESS_FIXTURE for a private Redis 7+ fixture:", err)
	}
	var fixture struct {
		Address, Username, Password string
		DB                          int
	}
	if err := json.Unmarshal(raw, &fixture); err != nil || fixture.Address == "" || fixture.Password == "" || fixture.DB != 1 {
		t.Fatal("invalid private Redis fixture (address/password/db=1 required)")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	t.Cleanup(cancel)
	client := redis.NewClient(&redis.Options{
		Addr: fixture.Address, Username: fixture.Username, Password: fixture.Password, DB: fixture.DB,
		MaxRetries: -1, ContextTimeoutEnabled: true, DialTimeout: time.Second,
		ReadTimeout: time.Second, WriteTimeout: time.Second, PoolSize: 8,
		Protocol: 2, DisableIdentity: true,
	})
	t.Cleanup(func() { _ = client.Close() })
	if err := client.Ping(ctx).Err(); err != nil {
		t.Fatal("private Redis unavailable:", err)
	}
	return ctx, client
}

func readinessIntegrationNonce(t *testing.T) string {
	t.Helper()
	var nonce [16]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		t.Fatal(err)
	}
	return hex.EncodeToString(nonce[:])
}

type readinessKeyState struct {
	Type, Dump string
	ExpiresAt  int64
}

// Full keyset, binary serialized values, types, and absolute millisecond
// expiries, with no readiness-key filtering or relative-TTL tolerances.
func readinessSnapshot(ctx context.Context, client *redis.Client) (map[string]readinessKeyState, error) {
	keys, err := client.Keys(ctx, "*").Result()
	if err != nil {
		return nil, err
	}
	state := make(map[string]readinessKeyState, len(keys))
	for _, key := range keys {
		typ, err := client.Type(ctx, key).Result()
		if err != nil {
			return nil, err
		}
		dump, err := client.Dump(ctx, key).Result()
		if err != nil {
			return nil, err
		}
		expiry, err := client.Do(ctx, "PEXPIRETIME", key).Int64()
		if err != nil {
			return nil, err
		}
		state[key] = readinessKeyState{Type: typ, Dump: dump, ExpiresAt: expiry}
	}
	return state, nil
}

func readinessRequireSnapshot(t *testing.T, ctx context.Context, client *redis.Client, want map[string]readinessKeyState) {
	t.Helper()
	got, err := readinessSnapshot(ctx, client)
	if err != nil {
		t.Fatal("full snapshot failed:", err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("full keyset/type/DUMP/absolute expiry changed (got %d keys; want %d)", len(got), len(want))
	}
}

// Wrap actual I/O for command accounting and tightly scoped fault injection.
// This hook never supplies a synthetic response or bypasses Redis execution.
type readinessWireHook struct {
	mu        sync.Mutex
	keys      []string
	transform func(string) string
}

func (h *readinessWireHook) DialHook(next redis.DialHook) redis.DialHook { return next }
func (h *readinessWireHook) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return next
}
func (h *readinessWireHook) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		if cmd.Name() == "eval" {
			args := cmd.Args()
			h.mu.Lock()
			h.keys = append(h.keys, args[3].(string))
			h.mu.Unlock()
			if h.transform != nil {
				args[1] = h.transform(args[1].(string))
			}
		}
		return next(ctx, cmd)
	}
}

func (h *readinessWireHook) observedKeys() []string {
	h.mu.Lock()
	defer h.mu.Unlock()
	return append([]string(nil), h.keys...)
}

func readinessObservedClient(t *testing.T, admin *redis.Client, transform func(string) string) (*redis.Client, *readinessWireHook) {
	t.Helper()
	options := *admin.Options()
	options.MaxRetries = -1 // Options() contains normalized zero, which would re-enable defaults.
	client := redis.NewClient(&options)
	t.Cleanup(func() { _ = client.Close() })
	hook := &readinessWireHook{transform: transform}
	client.AddHook(hook)
	return client, hook
}

func TestRedisProbeIntegrationAtomicSnapshots(t *testing.T) {
	ctx, admin := readinessIntegrationClient(t)
	original, err := readinessSnapshot(ctx, admin)
	if err != nil {
		t.Fatal(err)
	}
	prefix := "assozeta:readiness-test:" + readinessIntegrationNonce(t)
	keys := []string{prefix + ":binary", prefix + ":list", prefix + ":hash", prefix + ":set", prefix + ":zset",
		"assozeta:go:readiness:" + readinessIntegrationNonce(t)}
	defer func() {
		if err := admin.Del(ctx, keys...).Err(); err != nil {
			t.Error("fixture cleanup:", err)
		}
		readinessRequireSnapshot(t, ctx, admin, original)
	}()
	commands := []error{
		admin.Set(ctx, keys[0], "\x00\xff\x80Django-pickle\r\n", 10*time.Minute).Err(),
		admin.RPush(ctx, keys[1], "a", "\x00b").Err(),
		admin.HSet(ctx, keys[2], "field", "\xffvalue").Err(),
		admin.SAdd(ctx, keys[3], "member", "\x00member").Err(),
		admin.ZAdd(ctx, keys[4], redis.Z{Score: 1.25, Member: "member"}).Err(),
		admin.Set(ctx, keys[5], "existing readiness-shaped data", 10*time.Minute).Err(),
		admin.PExpire(ctx, keys[2], 10*time.Minute).Err(),
	}
	for _, err := range commands {
		if err != nil {
			t.Fatal("fixture seed:", err)
		}
	}
	before, err := readinessSnapshot(ctx, admin)
	if err != nil {
		t.Fatal(err)
	}
	client, hook := readinessObservedClient(t, admin, nil)
	probe := NewRedisProbe(client)
	if err := probe.Check(ctx); err != nil {
		t.Fatal(err)
	}
	readinessRequireSnapshot(t, ctx, admin, before)

	// Observe from a separate connection while eight callers continuously
	// execute real EVALs. Baseline fixtures live much longer than this test.
	var group sync.WaitGroup
	start := make(chan struct{})
	for range 8 {
		group.Add(1)
		go func() {
			defer group.Done()
			<-start
			for range 128 {
				if err := probe.Check(ctx); err != nil {
					t.Error("concurrent readiness:", err)
					return
				}
			}
		}()
	}
	close(start)
	// Ensure workers are joined even if a strict snapshot fails.
	func() {
		defer group.Wait()
		for range 64 {
			readinessRequireSnapshot(t, ctx, admin, before)
		}
	}()
	readinessRequireSnapshot(t, ctx, admin, before)
	observed := hook.observedKeys()
	if len(observed) != 1025 {
		t.Fatalf("unexpected EVAL count: %d", len(observed))
	}
	seen := make(map[string]bool, len(observed))
	for _, key := range observed {
		if seen[key] {
			t.Fatal("concurrent nonce reused")
		}
		seen[key] = true
	}
	if client.PoolStats().TotalConns > 8 {
		t.Fatal("probe exceeded bounded connection pool")
	}
}

func TestRedisProbeIntegrationCollisionPreservesExistingKey(t *testing.T) {
	ctx, admin := readinessIntegrationClient(t)
	key := "assozeta:go:readiness:" + readinessIntegrationNonce(t)
	if err := admin.HSet(ctx, key, "existing", "\x00\xffdata").Err(); err != nil {
		t.Fatal(err)
	}
	defer admin.Del(ctx, key)
	if err := admin.PExpire(ctx, key, 10*time.Minute).Err(); err != nil {
		t.Fatal(err)
	}
	before, err := readinessSnapshot(ctx, admin)
	if err != nil {
		t.Fatal(err)
	}
	// Force the otherwise astronomically unlikely collision using the exact
	// production script. No nonce generator seam is needed in production.
	if err := admin.Eval(ctx, redisReadinessScript, []string{key}, "ok").Err(); err == nil || !strings.Contains(err.Error(), "collision") {
		t.Fatalf("collision admitted: %v", err)
	}
	readinessRequireSnapshot(t, ctx, admin, before)
}

func TestRedisProbeIntegrationSnapshotControl(t *testing.T) {
	ctx, admin := readinessIntegrationClient(t)
	before, err := readinessSnapshot(ctx, admin)
	if err != nil {
		t.Fatal(err)
	}
	key := "assozeta:go:readiness:" + readinessIntegrationNonce(t)
	defer admin.Del(ctx, key)
	// A separate SET/GET/DEL can preserve the final snapshot but exposes a
	// key between commands. Prove that the full snapshot detects that control.
	if err := admin.Set(ctx, key, "ok", 10*time.Second).Err(); err != nil {
		t.Fatal(err)
	}
	during, err := readinessSnapshot(ctx, admin)
	if err != nil {
		t.Fatal(err)
	}
	if reflect.DeepEqual(during, before) || len(during) != len(before)+1 || during[key].Type != "string" || during[key].ExpiresAt <= 0 {
		t.Fatal("strict snapshot failed to detect a non-atomic readiness key")
	}
	if value, err := admin.Get(ctx, key).Result(); err != nil || value != "ok" {
		t.Fatalf("control readback: %q, %v", value, err)
	}
	if err := admin.Del(ctx, key).Err(); err != nil {
		t.Fatal(err)
	}
	readinessRequireSnapshot(t, ctx, admin, before)
}

func readinessIsACLDenial(err error, user, denied string) bool {
	if err == nil {
		return false
	}
	message := err.Error()
	switch denied {
	case "-eval":
		return message == "NOPERM this user has no permissions to run the 'eval' command" ||
			message == "NOPERM User "+user+" has no permissions to run the 'eval' command"
	case "resetkeys":
		return message == "NOPERM this user has no permissions to access one of the keys used as arguments" ||
			message == "NOPERM No permissions to access a key"
	case "-set", "-get", "-del":
		want := "ERR ACL failure in script: User " + user + " has no permissions to run the '" + strings.TrimPrefix(denied, "-") + "' command"
		// Redis 7.2.5 wraps redis.call's SET denial with script context;
		// redis.pcall's GET/DEL denials return the ACL error directly.
		digest := sha1.Sum([]byte(redisReadinessScript)) // Redis's script identifier, not a security check.
		return message == want || message == want+" script: "+hex.EncodeToString(digest[:])+", on @user_script:1."
	default:
		return false
	}
}

// Register before executing a probe: any later Fatal must still remove owned
// residue before the next subtest takes its baseline. Never delete a key that
// existed in the full baseline, even if a generated nonce happens to collide.
// Cleanup is separate from assertions, so unexpected residue still fails the
// unchanged full snapshot checks. Use a fresh bounded context after deadlines.
func readinessCleanupObservedKeys(t *testing.T, admin *redis.Client, hook *readinessWireHook, before map[string]readinessKeyState) {
	t.Helper()
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		for _, key := range hook.observedKeys() {
			if _, existed := before[key]; existed {
				continue
			}
			if err := admin.Del(ctx, key).Err(); err != nil {
				t.Error("owned readiness residue cleanup:", err)
			}
		}
	})
}

// Replay the actual Redis 7.2.5 diagnostics without sockets. Negative controls
// must reject unrelated errors, including a denial of the wrong command/user.
func TestRedisProbeACLDenialDiagnostic(t *testing.T) {
	const user = "az-readiness-test"
	const scriptLocation = " script: bf403876d8b5404350a77abf9a4aee122c364a41, on @user_script:1."
	const scriptDenial = "ERR ACL failure in script: User " + user + " has no permissions to run the "
	for _, tc := range []struct {
		name, denied, message string
		want                  bool
	}{
		{"eval", "-eval", "NOPERM this user has no permissions to run the 'eval' command", true},
		{"eval-named-user", "-eval", "NOPERM User " + user + " has no permissions to run the 'eval' command", true},
		{"eval-wrong-user", "-eval", "NOPERM User another-user has no permissions to run the 'eval' command", false},
		{"key-access-7.2", "resetkeys", "NOPERM No permissions to access a key", true},
		{"key-access", "resetkeys", "NOPERM this user has no permissions to access one of the keys used as arguments", true},
		{"write", "-set", scriptDenial + "'set' command" + scriptLocation, true},
		{"read", "-get", scriptDenial + "'get' command", true},
		{"delete", "-del", scriptDenial + "'del' command", true},
		{"generic-err", "-get", "ERR arbitrary script failure", false},
		{"generic-noperm", "-eval", "NOPERM unrelated failure", false},
		{"generic-cant-run", "-set", "ERR can't run arbitrary command", false},
		{"wrong-command", "-get", scriptDenial + "'set' command" + scriptLocation, false},
		{"wrong-user", "-del", strings.Replace(scriptDenial, user, "another-user", 1) + "'del' command", false},
		{"wrong-script", "-set", scriptDenial + "'set' command" + strings.Replace(scriptLocation, "bf403876", "00000000", 1), false},
		{"wrong-eval-command", "-eval", "NOPERM this user has no permissions to run the 'get' command", false},
		{"wrong-key-denial", "resetkeys", "NOPERM this user has no permissions to run the 'eval' command", false},
		{"unknown-denial", "-ping", "NOPERM this user has no permissions to run the 'ping' command", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := readinessIsACLDenial(errors.New(tc.message), user, tc.denied); got != tc.want {
				t.Fatalf("ACL diagnostic matched = %t, want %t: %s", got, tc.want, tc.message)
			}
		})
	}
	if readinessIsACLDenial(nil, user, "-eval") {
		t.Fatal("successful probe matched an ACL denial")
	}
}

func TestRedisProbeIntegrationACLFailures(t *testing.T) {
	ctx, admin := readinessIntegrationClient(t)
	for _, tc := range []struct {
		name, denied string
		residue      bool
	}{
		{"allowed", "", false},
		{"eval", "-eval", false},
		{"write", "-set", false},
		{"read", "-get", false}, // GET failure still executes DEL.
		{"delete", "-del", true},
		{"key-access", "resetkeys", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			before, err := readinessSnapshot(ctx, admin)
			if err != nil {
				t.Fatal(err)
			}
			user := "az-readiness-" + readinessIntegrationNonce(t)
			password := readinessIntegrationNonce(t)
			args := []any{"ACL", "SETUSER", user, "reset", "on", ">" + password, "~assozeta:go:readiness:*", "+@all"}
			if tc.denied != "" {
				args = append(args, tc.denied)
			}
			if err := admin.Do(ctx, args...).Err(); err != nil {
				t.Fatal("private fixture needs ACL admin privileges:", err)
			}
			defer func() {
				if err := admin.Do(ctx, "ACL", "DELUSER", user).Err(); err != nil {
					t.Error("ACL user cleanup:", err)
				}
			}()
			options := *admin.Options()
			options.Username, options.Password, options.MaxRetries = user, password, -1
			client := redis.NewClient(&options)
			defer client.Close()
			hook := &readinessWireHook{}
			client.AddHook(hook)
			readinessCleanupObservedKeys(t, admin, hook, before)
			err = NewRedisProbe(client).Check(ctx)
			if tc.denied == "" && err != nil {
				t.Fatal("permitted ACL probe failed:", err)
			}
			if tc.denied != "" && err == nil {
				t.Fatal("ACL failure reported ready")
			}
			if tc.denied != "" && !readinessIsACLDenial(err, user, tc.denied) {
				t.Fatalf("expected command/key ACL denial, got: %v", err)
			}
			observed := hook.observedKeys()
			if len(observed) != 1 {
				t.Fatalf("ACL probe retried: %d", len(observed))
			}
			if tc.residue {
				readinessRequireBoundedResidue(t, ctx, admin, observed[0])
				if err := admin.Del(ctx, observed[0]).Err(); err != nil {
					t.Fatal(err)
				}
			}
			readinessRequireSnapshot(t, ctx, admin, before)
		})
	}
}

func readinessRequireBoundedResidue(t *testing.T, ctx context.Context, admin *redis.Client, key string) {
	t.Helper()
	value, err := admin.Get(ctx, key).Result()
	if err != nil || value != "ok" {
		t.Fatalf("expected failed-script residue: %q, %v", value, err)
	}
	ttl, err := admin.PTTL(ctx, key).Result()
	if err != nil || ttl <= 0 || ttl > 10*time.Second {
		t.Fatalf("failed write lacks bounded expiry: %s, %v", ttl, err)
	}
}

func TestRedisProbeIntegrationScriptFailures(t *testing.T) {
	ctx, admin := readinessIntegrationClient(t)
	for _, tc := range []struct {
		name, marker, replacement, message string
		residue                            bool
	}{
		{"malformed-read", "local value = redis.pcall('GET', KEYS[1])", "local value = redis.pcall('GET', KEYS[1])\nvalue = 'wrong'", "readback", false},
		{"missing-read", "local value = redis.pcall('GET', KEYS[1])", "local value = redis.pcall('GET', KEYS[1])\nvalue = false", "readback", false},
		{"cleanup-count", "local deleted = redis.pcall('DEL', KEYS[1])", "local deleted = redis.pcall('DEL', KEYS[1])\ndeleted = 0", "cleanup", false},
		{"malformed-result", "\nreturn value", "\nreturn 1", "unexpected type", false},
		{"error-after-write", "local value = redis.pcall('GET', KEYS[1])", "error('injected after SET')\nlocal value = redis.pcall('GET', KEYS[1])", "injected after SET", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			before, err := readinessSnapshot(ctx, admin)
			if err != nil {
				t.Fatal(err)
			}
			client, hook := readinessObservedClient(t, admin, func(script string) string {
				if strings.Count(script, tc.marker) != 1 {
					t.Fatal("fault injection marker changed")
				}
				return strings.Replace(script, tc.marker, tc.replacement, 1)
			})
			readinessCleanupObservedKeys(t, admin, hook, before)
			err = NewRedisProbe(client).Check(ctx)
			if err == nil || !strings.Contains(err.Error(), tc.message) {
				t.Fatalf("script failure hidden: %v", err)
			}
			observed := hook.observedKeys()
			if len(observed) != 1 {
				t.Fatalf("script failure retried: %d", len(observed))
			}
			if tc.residue {
				readinessRequireBoundedResidue(t, ctx, admin, observed[0])
				// Verify the ten-second fallback really expires, rather than
				// merely trusting the TTL metadata. This wait is parent-only.
				deadline := time.NewTimer(11 * time.Second)
				defer deadline.Stop()
				ticker := time.NewTicker(50 * time.Millisecond)
				defer ticker.Stop()
			waitExpiry:
				for {
					select {
					case <-ctx.Done():
						t.Fatal(ctx.Err())
					case <-deadline.C:
						t.Fatal("failed-script key outlived ten-second TTL")
					case <-ticker.C:
						n, err := admin.Exists(ctx, observed[0]).Result()
						if err != nil {
							t.Fatal(err)
						}
						if n == 0 {
							break waitExpiry
						}
					}
				}
			}
			readinessRequireSnapshot(t, ctx, admin, before)
		})
	}
}

func TestRedisProbeIntegrationDeadlineAndRecovery(t *testing.T) {
	ctx, admin := readinessIntegrationClient(t)
	before, err := readinessSnapshot(ctx, admin)
	if err != nil {
		t.Fatal(err)
	}
	client, hook := readinessObservedClient(t, admin, nil)
	probe := NewRedisProbe(client)
	if err := probe.Check(ctx); err != nil { // Prewarm authentication/selection.
		t.Fatal(err)
	}
	cancelled, cancel := context.WithCancel(ctx)
	cancel()
	if err := probe.Check(cancelled); !errors.Is(err, context.Canceled) || len(hook.observedKeys()) != 1 {
		t.Fatalf("cancelled probe issued I/O or hid failure: %v", err)
	}
	if err := admin.Do(ctx, "CLIENT", "PAUSE", 200, "WRITE").Err(); err != nil {
		t.Fatal("private fixture needs CLIENT PAUSE:", err)
	}
	defer admin.Do(ctx, "CLIENT", "UNPAUSE")
	limited, stop := context.WithTimeout(ctx, 25*time.Millisecond)
	start := time.Now()
	err = probe.Check(limited)
	stop()
	if err == nil || time.Since(start) > time.Second || len(hook.observedKeys()) != 2 {
		t.Fatalf("deadline/retry bound failed: %v (%s), %d EVALs", err, time.Since(start), len(hook.observedKeys()))
	}
	// The timed-out EVAL may still execute on Redis. A later write is a
	// barrier after the pause; successful script cleanup must still be atomic.
	if err := probe.Check(ctx); err != nil {
		t.Fatal("pool failed to recover:", err)
	}
	readinessRequireSnapshot(t, ctx, admin, before)
	if err := client.Close(); err != nil {
		t.Fatal(err)
	}
	if err := probe.Check(ctx); err == nil {
		t.Fatal("closed client reported ready")
	}
	if got := len(hook.observedKeys()); got != 4 {
		t.Fatalf("unexpected command/retry count: %d", got)
	}
}
