//go:build googleintegration

package social

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"net/http"
	"net/url"
	"os"
	"reflect"
	"strconv"
	"testing"
	"time"
)

type googleFixtureHTTP struct {
	client *http.Client
	origin string
	index  int
	jwk    bool
	t      *testing.T
}

func (c *googleFixtureHTTP) Do(request *http.Request) (*http.Response, error) {
	base := *request.URL
	base.RawQuery = ""
	phase := map[string]string{GoogleCertsURL: "certs", GoogleTokenInfoURL: "tokeninfo", GoogleUserInfoURL: "userinfo"}[base.String()]
	if phase == "certs" {
		if c.jwk {
			phase = "jwks"
		}
	}
	if phase == "" || request.Method != http.MethodGet {
		c.t.Fatal("unexpected Google request destination")
	}
	copy := request.Clone(request.Context())
	target, err := url.Parse(c.origin + "/" + strconv.Itoa(c.index) + "/" + phase)
	if err != nil {
		c.t.Fatal("invalid fixture URL")
	}
	target.RawQuery = request.URL.RawQuery
	copy.URL = target
	if c.jwk {
		return c.client.Transport.RoundTrip(copy)
	}
	return c.client.Do(copy)
}

func (c *googleFixtureHTTP) RoundTrip(r *http.Request) (*http.Response, error) { return c.Do(r) }

func TestGoogleHTTPSInterop(t *testing.T) {
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_GOOGLE_FIXTURE"))
	if err != nil {
		t.Fatal("run TESTS/adapters/django/google_tokens.py")
	}
	var fixture struct {
		Now    int64
		Origin string
		Cases  []struct {
			Name            string
			Token, Audience any
			Valid, Error    bool
			Expected        map[string]any
		}
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	if d.Decode(&fixture) != nil || len(fixture.Cases) != 93 {
		t.Fatal("invalid Google fixture")
	}
	pem, err := os.ReadFile(os.Getenv("ASSOZETA_GOOGLE_CA"))
	if err != nil {
		t.Fatal("missing ephemeral trust root")
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(pem) {
		t.Fatal("invalid ephemeral trust root")
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	transport.TLSClientConfig = &tls.Config{RootCAs: pool, MinVersion: tls.VersionTLS12}
	defer transport.CloseIdleConnections()
	rewrite := &googleFixtureHTTP{client: &http.Client{Transport: transport, Timeout: 10 * time.Second}, origin: fixture.Origin, t: t}
	jwkRewrite := &googleFixtureHTTP{client: rewrite.client, origin: fixture.Origin, t: t, jwk: true}
	client := NewGoogle(rewrite, jwkRewrite, func() time.Time { return time.Unix(fixture.Now, 500000000) }, 2)
	for index, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			rewrite.index = index
			jwkRewrite.index = index
			info, valid, err := client.Verify(context.Background(), tc.Token, tc.Audience)
			if valid != tc.Valid || (err != nil) != tc.Error || !reflect.DeepEqual(info, tc.Expected) {
				t.Fatalf("valid=%v error=%v identity matches=%v", valid, err != nil, reflect.DeepEqual(info, tc.Expected))
			}
		})
	}
}
