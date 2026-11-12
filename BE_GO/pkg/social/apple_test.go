package social

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"math/big"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	jwt "github.com/golang-jwt/jwt/v5"
)

type httpCall func(*http.Request) (*http.Response, error)

func (f httpCall) Do(r *http.Request) (*http.Response, error) { return f(r) }
func appleKey(t *testing.T) (*rsa.PrivateKey, map[string]any) {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	return key, map[string]any{"kid": "fixture", "kty": "RSA", "n": base64.RawURLEncoding.EncodeToString(key.N.Bytes()), "e": base64.RawURLEncoding.EncodeToString(big.NewInt(int64(key.E)).Bytes())}
}
func appleToken(t *testing.T, key *rsa.PrivateKey, claims jwt.MapClaims) string {
	t.Helper()
	token := jwt.NewWithClaims(jwt.SigningMethodRS256, claims)
	token.Header["kid"] = "fixture"
	encoded, err := token.SignedString(key)
	if err != nil {
		t.Fatal(err)
	}
	return encoded
}
func TestAppleSignatureClaimsAndRawIdentity(t *testing.T) {
	key, jwk := appleKey(t)
	other, _ := appleKey(t)
	for _, tc := range []struct {
		name            string
		patch           jwt.MapClaims
		remove          string
		aud             any
		wrongKey, valid bool
	}{
		{name: "valid", valid: true},
		{name: "expired", patch: jwt.MapClaims{"exp": 1000}},
		{name: "fractional expiry truncates", patch: jwt.MapClaims{"exp": 1000.9}},
		{name: "future iat", patch: jwt.MapClaims{"iat": 1001}},
		{name: "future nbf", patch: jwt.MapClaims{"nbf": "1001"}},
		{name: "integer time strings", patch: jwt.MapClaims{"iat": "+1_000", "exp": " 2000 "}, valid: true},
		{name: "unicode time digits", patch: jwt.MapClaims{"iat": "١٠٠٠"}, valid: true},
		{name: "optional expiry", remove: "exp", valid: true},
		{name: "null time", patch: jwt.MapClaims{"iat": nil}},
		{name: "wrong issuer", patch: jwt.MapClaims{"iss": "https://wrong.example.test"}},
		{name: "missing issuer", remove: "iss"},
		{name: "wrong audience", patch: jwt.MapClaims{"aud": "other"}},
		{name: "audience list", patch: jwt.MapClaims{"aud": []any{"other", "client"}}, valid: true},
		{name: "invalid audience list", patch: jwt.MapClaims{"aud": []any{17, "client"}}},
		{name: "configured audience list", aud: []any{"other", "client"}, valid: true},
		{name: "numeric subject", patch: jwt.MapClaims{"sub": 17}},
		{name: "null subject", patch: jwt.MapClaims{"sub": nil}},
		{name: "missing subject", remove: "sub", valid: true},
		{name: "numeric jti", patch: jwt.MapClaims{"jti": 17}},
		{name: "unverified email retained", patch: jwt.MapClaims{"email_verified": false}, valid: true},
		{name: "null email retained", patch: jwt.MapClaims{"email": nil}, valid: true},
		{name: "wrong signature", wrongKey: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			claims := jwt.MapClaims{"iss": "https://appleid.apple.com", "aud": "client", "iat": 900, "exp": 2000, "sub": "subject", "email": "person@example.test", "email_verified": "true"}
			for k, v := range tc.patch {
				claims[k] = v
			}
			if tc.remove != "" {
				delete(claims, tc.remove)
			}
			signing := key
			if tc.wrongKey {
				signing = other
			}
			encoded := appleToken(t, signing, claims)
			calls := 0
			body, _ := json.Marshal(map[string]any{"keys": []any{jwk}})
			client := NewApple(httpCall(func(r *http.Request) (*http.Response, error) {
				calls++
				if r.URL.String() != AppleKeysURL || r.Method != "GET" || r.Header.Get("Authorization") != "" {
					t.Error("unexpected provider request")
				}
				deadline, ok := r.Context().Deadline()
				if !ok || time.Until(deadline) > 10*time.Second {
					t.Error("missing request deadline")
				}
				return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(string(body)))}, nil
			}), func() time.Time { return time.Unix(1000, 500000000) }, 2)
			aud := tc.aud
			if aud == nil {
				aud = "client"
			}
			info, valid, err := client.Verify(context.Background(), encoded, aud)
			if err != nil || valid != tc.valid || calls != 1 {
				t.Fatalf("valid=%v err=%v calls=%d", valid, err, calls)
			}
			if !valid {
				if info != nil {
					t.Fatal("rejected token leaked identity")
				}
				return
			}
			if info["email"] != claims["email"] || info["email_verified"] != claims["email_verified"] || info["sub"] != claims["sub"] || info["first_name"] != "" || info["last_name"] != "" {
				t.Fatal("provider identity changed")
			}
		})
	}
}

type closeReader struct {
	io.Reader
	closed *atomic.Int32
}

func (r closeReader) Close() error { r.closed.Add(1); return nil }
func TestAppleHTTPFailuresAndDisabledAdmission(t *testing.T) {
	for _, aud := range []any{nil, "", false, json.Number("0"), []any{}, map[string]any{}} {
		client := NewApple(httpCall(func(*http.Request) (*http.Response, error) {
			t.Fatal("disabled client contacted provider")
			return nil, nil
		}), time.Now, 1)
		if info, valid, err := client.Verify(context.Background(), "anything", aud); info != nil || valid || err != nil {
			t.Fatal("disabled client accepted")
		}
	}
	for _, body := range []string{"not JSON", `null`, `{}`, `{"keys":null}`, `{"keys":[{}]}`, strings.Repeat("x", MaxAppleKeysBytes+1)} {
		var closed atomic.Int32
		client := NewApple(httpCall(func(*http.Request) (*http.Response, error) {
			return &http.Response{StatusCode: 200, Body: closeReader{strings.NewReader(body), &closed}}, nil
		}), time.Now, 1)
		if info, valid, err := client.Verify(context.Background(), "invalid", "client"); info != nil || valid || err != nil {
			t.Fatal("bad key response admitted")
		}
		if closed.Load() != 1 {
			t.Fatal("response body retained")
		}
	}
	client := NewApple(httpCall(func(*http.Request) (*http.Response, error) { return nil, errors.New("provider failure must not leak") }), time.Now, 1)
	if info, valid, err := client.Verify(context.Background(), "invalid", "client"); info != nil || valid || err != nil {
		t.Fatal("network failure admitted or leaked")
	}
}

func TestAppleBoundCancellationAndReuse(t *testing.T) {
	var calls, active, peak atomic.Int32
	entered := make(chan struct{}, 3)
	client := NewApple(httpCall(func(r *http.Request) (*http.Response, error) {
		calls.Add(1)
		n := active.Add(1)
		defer active.Add(-1)
		for old := peak.Load(); n > old && !peak.CompareAndSwap(old, n); old = peak.Load() {
		}
		entered <- struct{}{}
		<-r.Context().Done()
		return nil, r.Context().Err()
	}), time.Now, 2)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var wg sync.WaitGroup
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			info, valid, err := client.Verify(ctx, "token", "client")
			if info != nil || valid || !errors.Is(err, context.Canceled) {
				t.Error("running cancellation lost")
			}
		}()
	}
	for i := 0; i < 2; i++ {
		select {
		case <-entered:
		case <-time.After(2 * time.Second):
			t.Fatal("provider did not start")
		}
	}
	waiting, stop := context.WithTimeout(context.Background(), 30*time.Millisecond)
	defer stop()
	if _, valid, err := client.Verify(waiting, "token", "client"); valid || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("queued cancellation lost")
	}
	if calls.Load() != 2 {
		t.Fatal("admission bound exceeded")
	}
	cancel()
	wg.Wait()
	retry, done := context.WithCancel(context.Background())
	finished := make(chan error, 1)
	go func() { _, _, err := client.Verify(retry, "token", "client"); finished <- err }()
	select {
	case <-entered:
	case <-time.After(2 * time.Second):
		t.Fatal("slot not released")
	}
	done()
	if !errors.Is(<-finished, context.Canceled) || peak.Load() != 2 || calls.Load() != 3 || active.Load() != 0 {
		t.Fatal("provider bound or reuse failed")
	}
}

type countedAppleBody struct {
	reader *strings.Reader
	read   int
	closed bool
}

func (b *countedAppleBody) Read(p []byte) (int, error) {
	n, err := b.reader.Read(p)
	b.read += n
	return n, err
}
func (b *countedAppleBody) Close() error { b.closed = true; return nil }

func TestAppleResourceLimits(t *testing.T) {
	body := &countedAppleBody{reader: strings.NewReader(strings.Repeat(" ", MaxAppleKeysBytes+4096))}
	client := NewApple(httpCall(func(*http.Request) (*http.Response, error) { return &http.Response{Body: body}, nil }), time.Now, 1)
	if info, valid, err := client.Verify(context.Background(), "token", "client"); info != nil || valid || err != nil {
		t.Fatal("oversized response admitted")
	}
	if body.read != MaxAppleKeysBytes+1 || !body.closed {
		t.Fatal("key response read not bounded", body.read, body.closed)
	}
	key, jwk := appleKey(t)
	oversized := map[string]any{"kty": "RSA", "n": base64.RawURLEncoding.EncodeToString(new(big.Int).Lsh(big.NewInt(1), maxAppleRSAKeyBits).Bytes()), "e": jwk["e"]}
	if _, err := applePublicKey(oversized); err == nil {
		t.Fatal("oversized RSA modulus admitted")
	}
	document, _ := json.Marshal(map[string]any{"keys": []any{jwk}})
	client = NewApple(httpCall(func(*http.Request) (*http.Response, error) {
		return &http.Response{Body: io.NopCloser(strings.NewReader(string(document)))}, nil
	}), func() time.Time { return time.Unix(1000, 0) }, 1)
	for _, tc := range []struct {
		size  int
		valid bool
	}{{maxAppleTokenBytes / 2, true}, {maxAppleTokenBytes, false}} {
		token := appleToken(t, key, jwt.MapClaims{"iss": "https://appleid.apple.com", "aud": "client", "exp": 2000, "unused": strings.Repeat("x", tc.size)})
		if _, valid, err := client.Verify(context.Background(), token, "client"); err != nil || valid != tc.valid {
			t.Fatal("token budget differs", valid, err)
		}
	}
}
