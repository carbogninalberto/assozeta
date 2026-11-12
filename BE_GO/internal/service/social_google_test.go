package service

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"errors"
	"net/http"
	"net/http/httptest"
	"net/url"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/pkg/social"
	jwt "github.com/golang-jwt/jwt/v5"
)

type localGoogleHTTP struct {
	client *http.Client
	target *url.URL
	t      *testing.T
}

func (c localGoogleHTTP) request(r *http.Request) *http.Request {
	copy := r.Clone(r.Context())
	target := *c.target
	target.RawQuery = r.URL.RawQuery
	switch r.URL.Scheme + "://" + r.URL.Host + r.URL.Path {
	case social.GoogleCertsURL:
		target.Path = "/certs"
	case social.GoogleTokenInfoURL:
		target.Path = "/tokeninfo"
	default:
		c.t.Fatal("unexpected Google destination")
	}
	copy.URL = &target
	return copy
}
func (c localGoogleHTTP) Do(r *http.Request) (*http.Response, error) {
	return c.client.Do(c.request(r))
}
func (c localGoogleHTTP) RoundTrip(r *http.Request) (*http.Response, error) {
	return c.client.Transport.RoundTrip(c.request(r))
}

func TestSocialProvidersWithRealGoogleVerification(t *testing.T) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	der, err := x509.MarshalPKIXPublicKey(&key.PublicKey)
	if err != nil {
		t.Fatal(err)
	}
	public := string(pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: der}))
	var calls atomic.Int32
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.Method != "GET" || r.Header.Get("Authorization") != "" {
			t.Error("unexpected Google certificate/access-info request")
		}
		if r.URL.Path == "/tokeninfo" {
			w.WriteHeader(401)
			w.Write([]byte(`{}`))
			return
		}
		if r.URL.RawQuery != "" {
			t.Error("credential leaked into certificate request")
		}
		json.NewEncoder(w).Encode(map[string]string{"fixture": public})
	}))
	defer server.Close()
	target, err := url.Parse(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	transport := localGoogleHTTP{server.Client(), target, t}
	google := social.NewGoogle(transport, transport, func() time.Time { return time.Unix(1000, 0) }, 2)
	ids := &socialIDs{value: "first"}
	flow := NewSocialProviders(ids, google, nil)
	tokenFor := func(audience string) string {
		token := jwt.NewWithClaims(jwt.SigningMethodRS256, jwt.MapClaims{"iss": "accounts.google.com", "aud": audience, "iat": 900, "exp": 2000, "email": "person@example.test"})
		token.Header["kid"] = "fixture"
		raw, err := token.SignedString(key)
		if err != nil {
			t.Fatal(err)
		}
		return raw
	}
	first := tokenFor("first")
	if identity, valid, err := flow.Verify(context.Background(), "google", first); err != nil || !valid || identity.Email != "person@example.test" {
		t.Fatal("configured Google verification failed")
	}
	ids.value = "second"
	if identity, valid, err := flow.Verify(context.Background(), "google", first); err != nil || valid || identity.Email != nil {
		t.Fatal("stale Google audience accepted")
	}
	if identity, valid, err := flow.Verify(context.Background(), "google", tokenFor("second")); err != nil || !valid || identity.Email != "person@example.test" {
		t.Fatal("new Google audience ignored")
	}
	ids.value = ""
	if identity, valid, err := flow.Verify(context.Background(), "google", first); err != nil || valid || identity.Email != nil {
		t.Fatal("disabled provider admitted")
	}
	if calls.Load() != 4 || len(ids.providers) != 4 {
		t.Fatal("Google request/configuration read counts differ")
	}
	ids.err = errors.New("configuration unavailable")
	if _, valid, err := flow.Verify(context.Background(), "google", first); valid || !errors.Is(err, ids.err) || calls.Load() != 4 {
		t.Fatal("configuration failure contacted Google")
	}
}
