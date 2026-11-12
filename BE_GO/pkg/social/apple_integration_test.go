//go:build appleintegration

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

type appleFixtureHTTP struct {
	client *http.Client
	target string
	t      *testing.T
	calls  int
}

func (c *appleFixtureHTTP) Do(request *http.Request) (*http.Response, error) {
	c.calls++
	if request.URL.String() != AppleKeysURL || request.Method != "GET" || request.Header.Get("Authorization") != "" {
		c.t.Fatal("Apple request destination or authorization changed")
	}
	copy := request.Clone(request.Context())
	target, err := url.Parse(c.target)
	if err != nil {
		c.t.Fatal("invalid local fixture URL")
	}
	copy.URL = target
	return c.client.Do(copy)
}

func TestAppleHTTPSInterop(t *testing.T) {
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_APPLE_FIXTURE"))
	if err != nil {
		t.Fatal("run TESTS/adapters/django/apple_tokens.py")
	}
	var fixture struct {
		Now    int64
		Origin string
		Cases  []struct {
			Name            string
			Token, Audience any
			Valid           bool
			Expected        map[string]any
		}
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	if decoder.Decode(&fixture) != nil || len(fixture.Cases) != 89 {
		t.Fatal("invalid Apple fixture")
	}
	pem, err := os.ReadFile(os.Getenv("ASSOZETA_APPLE_CA"))
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
	httpClient := &http.Client{Transport: transport, Timeout: 10 * time.Second}
	rewrite := &appleFixtureHTTP{client: httpClient, t: t}
	// Reuse the verifier across every case so the two-key rotation cases also
	// reject accidental caching of the previous key set.
	verifier := NewApple(rewrite, func() time.Time { return time.Unix(fixture.Now, 500000000) }, 2)
	for index, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			rewrite.target = fixture.Origin + "/case/" + strconv.Itoa(index)
			rewrite.calls = 0
			info, valid, err := verifier.Verify(context.Background(), tc.Token, tc.Audience)
			if err != nil || valid != tc.Valid || !reflect.DeepEqual(info, tc.Expected) {
				t.Fatalf("valid=%v err=%v identity matches=%v", valid, err, reflect.DeepEqual(info, tc.Expected))
			}
			want := 0
			if truthy(tc.Audience) {
				want = 1
			}
			if rewrite.calls != want {
				t.Fatal("provider admission count changed")
			}
		})
	}
}
