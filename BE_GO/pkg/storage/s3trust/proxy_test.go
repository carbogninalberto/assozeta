package s3trust

import (
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// Both TLS layers inherit the selected CA bundle in pinned Boto3, but verify
// different hostnames. A private real CONNECT relay must not become a target
// hostname check against the outer proxy certificate.
func TestHTTPAndHTTPSProxyDistinctHostnamesAndTrust(t *testing.T) {
	for _, tc := range []struct {
		name, scheme, bundle                                string
		badProxy, badTarget, valid                          bool
		connects                                            int32
		sameHost, missingProxyAKI, missingTargetAKI, absent bool
	}{
		{"https-both-roots", "https", "both", false, false, true, 1, false, false, false, false},
		{"https-target-root-only", "https", "target", false, false, false, 0, false, false, false, false},
		{"https-proxy-root-only", "https", "proxy", false, false, false, 1, false, false, false, false},
		{"https-proxy-name-rejected", "https", "both", true, false, false, 0, false, false, false, false},
		{"https-target-name-rejected", "https", "both", false, true, false, 1, false, false, false, false},
		{"https-explicit-empty", "https", "empty", true, true, true, 1, false, false, false, false},
		{"http-target-root", "http", "target", false, false, true, 1, false, false, false, false},
		{"http-target-name-rejected", "http", "target", false, true, false, 1, false, false, false, false},
		{"same-host-inner-ordinary", "https", "both", false, false, true, 1, true, false, true, false},
		{"same-host-outer-strict", "https", "both", false, false, false, 0, true, true, false, false},
		{"absent-policy-proxy", "https", "both", false, false, true, 1, true, false, false, true},
		{"absent-policy-outer-strict", "https", "both", false, false, false, 0, true, true, false, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			clearEnvironment(t)
			targetRoot, proxyRoot := rootCertificate(t), rootCertificate(t)
			template := x509.Certificate{NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour),
				ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}, KeyUsage: x509.KeyUsageDigitalSignature}
			targetTemplate := template
			targetTemplate.DNSNames = []string{"localhost"}
			if tc.sameHost {
				targetTemplate.IPAddresses = []net.IP{net.ParseIP("127.0.0.1")}
			}
			if tc.missingTargetAKI {
				targetTemplate.ExtraExtensions = []pkix.Extension{{Id: []int{2, 5, 29, 35}, Value: []byte{0x30, 0x00}}}
			}
			if tc.badTarget {
				targetTemplate.DNSNames = []string{"wrong.invalid"}
			}
			target := serveCertificate(t, issue(t, &targetRoot, targetTemplate))
			proxyTemplate := template
			proxyTemplate.IPAddresses = []net.IP{net.ParseIP("127.0.0.1")}
			if tc.badProxy {
				proxyTemplate.IPAddresses = []net.IP{net.ParseIP("127.0.0.2")}
			}
			if tc.missingProxyAKI {
				proxyTemplate.ExtraExtensions = []pkix.Extension{{Id: []int{2, 5, 29, 35}, Value: []byte{0x30, 0x00}}}
			}
			proxyCertificate := issue(t, &proxyRoot, proxyTemplate)
			var connects atomic.Int32
			var mu sync.Mutex
			connections := map[net.Conn]bool{}
			var group sync.WaitGroup
			proxy := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodConnect {
					w.WriteHeader(405)
					return
				}
				connects.Add(1)
				up, err := net.DialTimeout("tcp", target.Listener.Addr().String(), time.Second)
				if err != nil {
					t.Error(err)
					w.WriteHeader(502)
					return
				}
				down, _, err := w.(http.Hijacker).Hijack()
				if err != nil {
					up.Close()
					t.Error(err)
					return
				}
				mu.Lock()
				connections[up] = true
				connections[down] = true
				mu.Unlock()
				group.Add(1)
				defer group.Done()
				defer func() {
					up.Close()
					down.Close()
					mu.Lock()
					delete(connections, up)
					delete(connections, down)
					mu.Unlock()
				}()
				_, _ = io.WriteString(down, "HTTP/1.1 200 Connection Established\r\n\r\n")
				done := make(chan struct{})
				go func() { _, _ = io.Copy(up, down); up.Close(); close(done) }()
				_, _ = io.Copy(down, up)
				down.Close()
				<-done
			}))
			proxy.Config.ErrorLog = log.New(io.Discard, "", 0)
			if tc.scheme == "https" {
				proxy.TLS = &tls.Config{Certificates: []tls.Certificate{{Certificate: [][]byte{proxyCertificate.der}, PrivateKey: proxyCertificate.key}}}
				proxy.StartTLS()
			} else {
				proxy.Start()
			}
			t.Cleanup(func() {
				mu.Lock()
				for connection := range connections {
					connection.Close()
				}
				mu.Unlock()
				proxy.Close()
				group.Wait()
			})
			proxyURL, _ := url.Parse(proxy.URL)
			bundle := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: targetRoot.der})
			if tc.bundle == "proxy" {
				bundle = nil
			}
			if tc.bundle == "proxy" || tc.bundle == "both" {
				bundle = append(bundle, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: proxyRoot.der})...)
			}
			path := filepath.Join(t.TempDir(), "roots.pem")
			if err := os.WriteFile(path, bundle, 0600); err != nil {
				t.Fatal(err)
			}
			if tc.bundle == "empty" {
				path = ""
			}
			if tc.absent {
				// Test-only replacement of the immutable parsed pool lets the actual absent
				// policy handshake reach private fixture chains; no production hook is added.
				roots := x509.NewCertPool()
				roots.AddCert(targetRoot.cert)
				roots.AddCert(proxyRoot.cert)
				original := parsedDefaultRoots
				parsedDefaultRoots = func() *x509.CertPool { return roots }
				t.Cleanup(func() { parsedDefaultRoots = original })
			} else {
				t.Setenv("AWS_CA_BUNDLE", path)
			}
			base := http.DefaultTransport.(*http.Transport).Clone()
			var proxyCalls atomic.Int32
			base.Proxy = func(*http.Request) (*url.URL, error) { proxyCalls.Add(1); return proxyURL, nil }
			transport := Select(nil).Wrap(base)
			defer transport.CloseIdleConnections()
			targetURL := strings.Replace(target.URL, "127.0.0.1", "localhost", 1)
			if tc.sameHost {
				targetURL = target.URL
			}
			response, err := (&http.Client{Transport: transport, Timeout: 3 * time.Second}).Get(targetURL)
			if response != nil {
				response.Body.Close()
			}
			if (err == nil) != tc.valid {
				t.Fatalf("proxy request success=%v want%v: %v", err == nil, tc.valid, err)
			}
			if proxyCalls.Load() != 1 {
				t.Fatal("Proxy callback was not preserved exactly once", proxyCalls.Load())
			}
			if connects.Load() != tc.connects {
				t.Fatalf("CONNECT count=%d want%d", connects.Load(), tc.connects)
			}
		})
	}
}
