package s3trust

import (
	"encoding/pem"
	"net/http"
	"net/http/httptest"
	"net/http/httputil"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestHTTPForwardingProxyLegacyVerificationIsSelectedBeforeTLS(t *testing.T) {
	for _, mode := range []string{"absent", "valid", "wrong", "missing", "invalid", "empty"} {
		t.Run(mode, func(t *testing.T) {
			clearEnvironment(t)
			var targetCalls, proxyCalls atomic.Int32
			target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { targetCalls.Add(1); w.WriteHeader(204) }))
			defer target.Close()
			targetURL, _ := url.Parse(target.URL)
			forward := httputil.NewSingleHostReverseProxy(targetURL)
			proxy := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				proxyCalls.Add(1)
				if r.Method == http.MethodConnect {
					w.WriteHeader(501)
					return
				}
				forward.ServeHTTP(w, r)
			}))
			defer proxy.Close()
			proxyURL, _ := url.Parse(proxy.URL)
			path := filepath.Join(t.TempDir(), "configured.pem")
			if mode == "valid" {
				if err := os.WriteFile(path, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: proxy.Certificate().Raw}), 0600); err != nil {
					t.Fatal(err)
				}
			}
			if mode == "wrong" {
				root := rootCertificate(t)
				if err := os.WriteFile(path, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: root.der}), 0600); err != nil {
					t.Fatal(err)
				}
			}
			if mode == "invalid" {
				if err := os.WriteFile(path, []byte("invalid certificate"), 0600); err != nil {
					t.Fatal(err)
				}
			}
			if mode == "empty" {
				path = ""
			}
			if mode != "absent" {
				t.Setenv("AWS_CA_BUNDLE", path)
			}
			base := http.DefaultTransport.(*http.Transport).Clone()
			base.Proxy = http.ProxyURL(proxyURL)
			transport := Select(nil).Wrap(base)
			defer transport.CloseIdleConnections()
			client := &http.Client{Transport: transport, Timeout: 2 * time.Second}
			response, err := client.Get(target.URL)
			if err != nil {
				t.Fatal("HTTP target legacy proxy selection changed", err)
			}
			response.Body.Close()
			if response.StatusCode != 204 || targetCalls.Load() != 1 || proxyCalls.Load() != 1 {
				t.Fatal("actual HTTP target was not reached")
			}
			// The same nonempty bad configuration must fail before CONNECT when the
			// target changes to HTTPS; there is no generic TLS-error fallback.
			if mode == "wrong" || mode == "missing" || mode == "invalid" {
				response, err = client.Get(strings.Replace(target.URL, "http:", "https:", 1))
				if response != nil {
					response.Body.Close()
				}
				if err == nil || proxyCalls.Load() != 1 {
					t.Fatal("HTTPS target bypassed invalid nonempty trust")
				}
			}
			plain := http.DefaultTransport.(*http.Transport).Clone()
			plain.Proxy = nil
			direct := Select(nil).Wrap(plain)
			defer direct.CloseIdleConnections()
			response, err = (&http.Client{Transport: direct}).Get(target.URL)
			if err != nil {
				t.Fatal("direct HTTP loaded CA bundle", err)
			}
			response.Body.Close()
		})
	}
}
