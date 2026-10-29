package service

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"errors"
	"math/big"
	"net/http"
	"net/http/httptest"
	"net/url"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/pkg/social"
	jwt "github.com/golang-jwt/jwt/v5"
)

type localAppleHTTP struct {
	client *http.Client
	target *url.URL
	t      *testing.T
}

func (c localAppleHTTP) Do(r *http.Request) (*http.Response, error) {
	if r.URL.String() != social.AppleKeysURL {
		c.t.Fatal("unexpected provider URL")
	}
	copy := r.Clone(r.Context())
	copy.URL = c.target
	return c.client.Do(copy)
}

func TestSocialProvidersWithRealAppleVerification(t *testing.T) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	var calls atomic.Int32
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.Method != "GET" || r.Header.Get("Authorization") != "" {
			t.Error("token leaked into key request")
		}
		json.NewEncoder(w).Encode(map[string]any{"keys": []any{map[string]any{"kid": "fixture", "kty": "RSA", "n": base64.RawURLEncoding.EncodeToString(key.N.Bytes()), "e": base64.RawURLEncoding.EncodeToString(big.NewInt(int64(key.E)).Bytes())}}})
	}))
	defer server.Close()
	url, err := url.Parse(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	apple := social.NewApple(localAppleHTTP{server.Client(), url, t}, func() time.Time { return time.Unix(1000, 0) }, 2)
	ids := &socialIDs{value: "first"}
	flow := NewSocialProviders(ids, nil, apple)
	tokenFor := func(audience string) string {
		token := jwt.NewWithClaims(jwt.SigningMethodRS256, jwt.MapClaims{"iss": "https://appleid.apple.com", "aud": audience, "exp": 2000, "email": "person@example.test"})
		token.Header["kid"] = "fixture"
		raw, err := token.SignedString(key)
		if err != nil {
			t.Fatal(err)
		}
		return raw
	}
	first := tokenFor("first")
	if identity, valid, err := flow.Verify(context.Background(), "apple", first); err != nil || !valid || identity.Email != "person@example.test" {
		t.Fatal("configured Apple verification failed")
	}
	ids.value = "second"
	if identity, valid, err := flow.Verify(context.Background(), "apple", first); err != nil || valid || identity.Email != nil {
		t.Fatal("stale audience remained valid")
	}
	if identity, valid, err := flow.Verify(context.Background(), "apple", tokenFor("second")); err != nil || !valid || identity.Email != "person@example.test" {
		t.Fatal("changed audience not applied")
	}
	ids.value = ""
	if identity, valid, err := flow.Verify(context.Background(), "apple", first); err != nil || valid || identity.Email != nil {
		t.Fatal("disabled provider admitted")
	}
	if calls.Load() != 3 || len(ids.providers) != 4 {
		t.Fatal("configuration and provider read counts differ")
	}
	ids.err = errors.New("configuration unavailable")
	if _, valid, err := flow.Verify(context.Background(), "apple", first); valid || !errors.Is(err, ids.err) || calls.Load() != 3 {
		t.Fatal("configuration failure reached provider")
	}
}
