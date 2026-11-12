package jwt

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"encoding/pem"
	"errors"
	"strconv"
	"strings"
	"testing"
	"time"

	libjwt "github.com/golang-jwt/jwt/v5"
)

func testManager(t *testing.T) (*Manager, ed25519.PrivateKey, time.Time) {
	t.Helper()
	public, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := x509.MarshalPKCS8PrivateKey(private)
	if err != nil {
		t.Fatal(err)
	}
	pub, err := x509.MarshalPKIXPublicKey(public)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Date(2030, 1, 1, 0, 0, 0, 0, time.UTC)
	manager, err := New(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: raw}), pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: pub}), func() time.Time { return now })
	if err != nil {
		t.Fatal(err)
	}
	return manager, private, now
}
func signed(t *testing.T, key ed25519.PrivateKey, claims libjwt.MapClaims) string {
	t.Helper()
	token, err := libjwt.NewWithClaims(libjwt.SigningMethodEdDSA, claims).SignedString(key)
	if err != nil {
		t.Fatal(err)
	}
	return token
}
func TestIssuePreservesAssozetaClaimsAndLifetimes(t *testing.T) {
	m, _, now := testManager(t)
	pair, err := m.Issue("7b23bb0d-27a3-471a-bb05-2e9be79d52fb", 2, "member@example.test")
	if err != nil {
		t.Fatal(err)
	}
	if pair.ExpiresIn != 14400 || pair.TokenType != "Bearer" {
		t.Fatal("changed response contract")
	}
	for _, tc := range []struct {
		token, kind string
		lifetime    int64
	}{{pair.AccessToken, "access", 14400}, {pair.RefreshToken, "refresh", 2592000}} {
		claims, err := m.Verify(tc.token, tc.kind)
		if err != nil {
			t.Fatal(err)
		}
		if claims["iss"] != "https://bakney.com" || claims["aud"] != "bakney" || claims["email"] != "member@example.test" || claims["user_id"] != "7b23bb0d-27a3-471a-bb05-2e9be79d52fb" {
			t.Fatal("identity claims changed")
		}
		if claims["exp"].(interface{ String() string }).String() != strconv.FormatInt(now.Unix()+tc.lifetime, 10) {
			t.Fatal("wrong lifetime")
		}
		id, ok := claims["jti"].(string)
		if !ok || len(id) != 32 || strings.Contains(id, "-") {
			t.Fatal("jti is not uuid hex")
		}
	}
}
func TestRefreshUpdatesAccessIATAndPreservesRefreshAndCustomClaims(t *testing.T) {
	m, key, now := testManager(t)
	claims := libjwt.MapClaims{"token_type": "refresh", "exp": now.Unix() + 30, "iat": now.Unix() - 600, "jti": "original", "user_id": "user", "role": 3, "custom": map[string]any{"number": 9007199254740993}, "aud": "legacy", "iss": "legacy"}
	encoded := signed(t, key, claims)
	pair, err := m.Refresh(encoded)
	if err != nil {
		t.Fatal(err)
	}
	refresh, err := m.Verify(pair.RefreshToken, "refresh")
	if err != nil {
		t.Fatal(err)
	}
	access, err := m.Verify(pair.AccessToken, "access")
	if err != nil {
		t.Fatal(err)
	}
	if refresh["iat"].(interface{ String() string }).String() != strconv.FormatInt(now.Unix()-600, 10) || refresh["jti"] != "original" || refresh["exp"].(interface{ String() string }).String() != strconv.FormatInt(now.Unix()+30, 10) {
		t.Fatal("refresh was rotated")
	}
	if access["iat"].(interface{ String() string }).String() != strconv.FormatInt(now.Unix(), 10) || access["exp"].(interface{ String() string }).String() != strconv.FormatInt(now.Unix()+14400, 10) {
		t.Fatal("SimpleJWT copy semantics changed")
	}
	if access["jti"] == "original" || access["aud"] != "legacy" || access["iss"] != "legacy" {
		t.Fatal("access claims changed")
	}
	if access["custom"].(map[string]any)["number"].(interface{ String() string }).String() != "9007199254740993" {
		t.Fatal("claim precision lost")
	}
}
func TestVerifyRejectsInvalidTokensAndClaims(t *testing.T) {
	m, key, now := testManager(t)
	for _, tc := range []struct {
		name   string
		modify func(libjwt.MapClaims)
	}{
		{"missing exp", func(c libjwt.MapClaims) { delete(c, "exp") }},
		{"expiry boundary", func(c libjwt.MapClaims) { c["exp"] = now.Unix() }},
		{"future iat", func(c libjwt.MapClaims) { c["iat"] = now.Unix() + 1 }},
		{"future nbf", func(c libjwt.MapClaims) { c["nbf"] = now.Unix() + 1 }},
		{"missing jti", func(c libjwt.MapClaims) { delete(c, "jti") }},
		{"wrong jti type", func(c libjwt.MapClaims) { c["jti"] = 42 }},
		{"wrong sub type", func(c libjwt.MapClaims) { c["sub"] = 42 }},
		{"wrong token type", func(c libjwt.MapClaims) { c["token_type"] = "refresh" }},
		{"string exp", func(c libjwt.MapClaims) { c["exp"] = strconv.FormatInt(now.Unix()+1, 10) }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c := libjwt.MapClaims{"token_type": "access", "exp": now.Unix() + 60, "iat": now.Unix(), "jti": "id"}
			tc.modify(c)
			if _, err := m.Verify(signed(t, key, c), "access"); err == nil {
				t.Fatal("invalid claims accepted")
			}
		})
	}
	pair, _ := m.Issue("user", 1, "test@example.test")
	other, _, _ := testManager(t)
	if _, err := other.Verify(pair.AccessToken, "access"); err == nil {
		t.Fatal("foreign signature accepted")
	}
	for _, token := range []string{"", "invalid", "a.b.c", strings.TrimSuffix(pair.AccessToken, ".") + "x"} {
		if _, err := m.Verify(token, "access"); err == nil {
			t.Fatal("malformed token accepted")
		}
	}
	hs, _ := libjwt.NewWithClaims(libjwt.SigningMethodHS256, libjwt.MapClaims{"exp": now.Unix() + 60}).SignedString([]byte("secret"))
	if _, err := m.Verify(hs, "access"); err == nil {
		t.Fatal("algorithm confusion accepted")
	}
}
func TestRESTDoesNotInventIssuerOrAudienceEnforcement(t *testing.T) {
	m, key, now := testManager(t)
	c := libjwt.MapClaims{"token_type": "access", "exp": now.Unix() + 60, "jti": "id"}
	if _, err := m.Verify(signed(t, key, c), "access"); err != nil {
		t.Fatal("SimpleJWT does not require iat, issuer, audience or user_id at token decoding")
	}
	c["aud"] = "different"
	c["iss"] = "different"
	if _, err := m.Verify(signed(t, key, c), "access"); err != nil {
		t.Fatal("REST validation must match configured SimpleJWT; WebSockets enforce iss/aud separately")
	}
}
func TestRejectsInvalidAndMismatchedKeysWithoutDisclosure(t *testing.T) {
	for _, value := range [][]byte{nil, []byte("secret-key-content"), pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: []byte("secret-key-content")})} {
		_, err := New(value, value, time.Now)
		if err == nil || strings.Contains(err.Error(), "secret-key-content") {
			t.Fatal("invalid key handling")
		}
	}
}

func TestConcurrentIssuanceHasIndependentIdentifiers(t *testing.T) {
	manager, _, _ := testManager(t)
	results := make(chan string, 32)
	for range 32 {
		go func() {
			pair, err := manager.Issue("user", 2, "member@example.test")
			if err != nil {
				results <- ""
				return
			}
			claims, err := manager.Verify(pair.AccessToken, "access")
			if err != nil {
				results <- ""
				return
			}
			results <- claims["jti"].(string)
		}()
	}
	seen := map[string]bool{}
	for range 32 {
		id := <-results
		if id == "" || seen[id] {
			t.Fatal("concurrent token issuance failed or repeated an identifier")
		}
		seen[id] = true
	}
}

func TestExpiryFailureClassificationAndPrecedence(t *testing.T) {
	m, key, now := testManager(t)
	for _, tc := range []struct {
		name    string
		changes libjwt.MapClaims
		expired bool
	}{
		{"expiry boundary", nil, true},
		{"past string expiry", libjwt.MapClaims{"exp": "1"}, true},
		{"future iat precedes expiry", libjwt.MapClaims{"iat": now.Unix() + 1}, false},
		{"future nbf precedes expiry", libjwt.MapClaims{"nbf": now.Unix() + 1}, false},
		{"invalid iat precedes expiry", libjwt.MapClaims{"iat": nil}, false},
		{"expiry precedes subject type", libjwt.MapClaims{"sub": 42}, true},
		{"expiry precedes token type", libjwt.MapClaims{"token_type": "refresh"}, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			claims := libjwt.MapClaims{"token_type": "access", "exp": now.Unix(), "iat": now.Unix(), "jti": "fixture"}
			for k, v := range tc.changes {
				claims[k] = v
			}
			_, err := m.Verify(signed(t, key, claims), "access")
			if !errors.Is(err, ErrInvalidToken) || errors.Is(err, ErrExpiredToken) != tc.expired {
				t.Fatal("expiry classification differs", err)
			}
		})
	}
	other, _, _ := testManager(t)
	_, err := other.Verify(signed(t, key, libjwt.MapClaims{"token_type": "access", "exp": 1, "jti": "fixture"}), "access")
	if !errors.Is(err, ErrInvalidToken) || errors.Is(err, ErrExpiredToken) {
		t.Fatal("untrusted expiry exposed before signature validation")
	}
}
