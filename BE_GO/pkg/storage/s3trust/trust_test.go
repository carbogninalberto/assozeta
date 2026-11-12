package s3trust

import (
	"context"
	"crypto/tls"
	"encoding/pem"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func clearEnvironment(t *testing.T) {
	t.Helper()
	for _, key := range []string{"AWS_CA_BUNDLE", "REQUESTS_CA_BUNDLE"} {
		old, present := os.LookupEnv(key)
		_ = os.Unsetenv(key)
		t.Cleanup(func() {
			if present {
				_ = os.Setenv(key, old)
			} else {
				_ = os.Unsetenv(key)
			}
		})
	}
}

func TestTrustSelectionPresenceAndRedaction(t *testing.T) {
	clearEnvironment(t)
	shared := "private-profile-path"
	for _, tc := range []struct {
		name          string
		aws, requests *string
		shared        *string
		configured    bool
		path          string
	}{
		{"absent", nil, nil, nil, false, ""},
		{"profile", nil, nil, &shared, true, shared},
		{"requests", nil, stringPointer("private-request-path"), nil, true, "private-request-path"},
		{"aws", stringPointer("private-aws-path"), stringPointer("wrong"), &shared, true, "private-aws-path"},
		{"empty-aws", stringPointer(""), stringPointer("wrong"), &shared, true, ""},
		{"empty-profile", nil, stringPointer("wrong"), stringPointer(""), true, ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if tc.aws != nil {
				t.Setenv("AWS_CA_BUNDLE", *tc.aws)
			}
			if tc.requests != nil {
				t.Setenv("REQUESTS_CA_BUNDLE", *tc.requests)
			}
			p := Select(tc.shared)
			if p.configured != tc.configured || p.path != tc.path {
				t.Fatal("selection differs")
			}
			if fmt.Sprint(p) != "storage TLS trust (redacted)" || fmt.Sprintf("%#v", p) != "storage TLS trust (redacted)" {
				t.Fatal("trust selection leaked")
			}
		})
	}
}

func stringPointer(value string) *string { return &value }

func TestLazyHTTPSOnlyFailureAndSameClientRecovery(t *testing.T) {
	clearEnvironment(t)
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(204) }))
	defer server.Close()
	plain := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(204) }))
	defer plain.Close()
	path := filepath.Join(t.TempDir(), "private-ca.pem")
	t.Setenv("AWS_CA_BUNDLE", path)
	base := http.DefaultTransport.(*http.Transport).Clone()
	wrapped := Select(nil).Wrap(base)
	defer wrapped.CloseIdleConnections()
	client := &http.Client{Transport: wrapped}
	request := func(url string) error {
		response, err := client.Get(url)
		if response != nil {
			response.Body.Close()
		}
		return err
	}
	if err := request(plain.URL); err != nil {
		t.Fatalf("HTTP loaded missing CA: %v", err)
	}
	if err := request(server.URL); err == nil {
		t.Fatal("missing CA allowed HTTPS")
	}
	if err := os.WriteFile(path, []byte("not a certificate"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := request(server.URL); err == nil {
		t.Fatal("invalid CA allowed HTTPS")
	}
	cert := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: server.Certificate().Raw})
	if err := os.WriteFile(path, cert, 0600); err != nil {
		t.Fatal(err)
	}
	if err := request(server.URL); err != nil {
		t.Fatalf("same client failed after CA repair: %v", err)
	}
	if err := os.WriteFile(path, []byte("broken replacement"), 0600); err != nil {
		t.Fatal(err)
	}
	// Django loads roots for a new TLS connection, not each keepalive request.
	if err := request(server.URL); err != nil {
		t.Fatalf("live connection unexpectedly reloaded CA: %v", err)
	}
	wrapped.CloseIdleConnections()
	if err := request(server.URL); err == nil {
		t.Fatal("cached valid CA bypassed broken replacement")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, server.URL, nil)
	if _, err := client.Do(req); err == nil {
		t.Fatal("cancellation ignored")
	}
}

func TestExplicitEmptyOnlyAndRootReplacement(t *testing.T) {
	clearEnvironment(t)
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	defer server.Close()
	for _, empty := range []bool{false, true} {
		t.Run(fmt.Sprint(empty), func(t *testing.T) {
			if empty {
				t.Setenv("AWS_CA_BUNDLE", "")
			}
			base := http.DefaultTransport.(*http.Transport).Clone()
			base.TLSClientConfig = &tls.Config{MinVersion: tls.VersionTLS12}
			wrapped := Select(nil).Wrap(base)
			defer wrapped.CloseIdleConnections()
			response, err := (&http.Client{Transport: wrapped}).Get(server.URL)
			if response != nil {
				response.Body.Close()
			}
			if (err == nil) != empty {
				t.Fatal("absent/empty trust boundary differs")
			}
			if base.TLSClientConfig.InsecureSkipVerify {
				t.Fatal("shared transport mutated")
			}
		})
	}
}
