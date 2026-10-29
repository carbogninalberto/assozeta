package s3trust

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"errors"
	"io"
	"log"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

type certificate struct {
	cert *x509.Certificate
	key  ed25519.PrivateKey
	der  []byte
}

func issue(t *testing.T, parent *certificate, template x509.Certificate) certificate {
	t.Helper()
	public, private, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	if template.SerialNumber == nil {
		template.SerialNumber = big.NewInt(1)
	}
	if parent == nil {
		parent = &certificate{cert: &template, key: private}
	}
	der, err := x509.CreateCertificate(rand.Reader, &template, parent.cert, public, parent.key)
	if err != nil {
		t.Fatal(err)
	}
	parsed, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	return certificate{parsed, private, der}
}

func rootCertificate(t *testing.T) certificate {
	return issue(t, nil, x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "owned root"},
		NotBefore: time.Now().Add(-96 * time.Hour), NotAfter: time.Now().Add(96 * time.Hour), IsCA: true, BasicConstraintsValid: true,
		KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageCRLSign})
}

func serveCertificate(t *testing.T, leaf certificate, chain ...[]byte) *httptest.Server {
	t.Helper()
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(204) }))
	server.Config.ErrorLog = log.New(io.Discard, "", 0)
	server.TLS = &tls.Config{MinVersion: tls.VersionTLS12, MaxVersion: tls.VersionTLS12,
		Certificates: []tls.Certificate{{Certificate: append([][]byte{leaf.der}, chain...), PrivateKey: leaf.key}}}
	server.StartTLS()
	t.Cleanup(server.Close)
	return server
}

func TestFullCertificateVerificationAndConfiguredClock(t *testing.T) {
	clearEnvironment(t)
	root := rootCertificate(t)
	path := filepath.Join(t.TempDir(), "ca.pem")
	if err := os.WriteFile(path, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: root.der}), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("AWS_CA_BUNDLE", path)
	for _, tc := range []struct {
		name   string
		dns    bool
		change func(*x509.Certificate)
		clock  func() time.Time
		valid  bool
	}{
		{name: "IP-SAN", valid: true},
		{name: "DNS-SAN", dns: true, valid: true},
		{name: "wrong-IP", change: func(c *x509.Certificate) { c.IPAddresses = []net.IP{net.ParseIP("127.0.0.2")} }},
		{name: "DNS-does-not-authorize-IP", change: func(c *x509.Certificate) { c.IPAddresses = nil }},
		{name: "wrong-DNS", dns: true, change: func(c *x509.Certificate) { c.DNSNames = []string{"other.invalid"} }},
		{name: "expired", change: func(c *x509.Certificate) {
			c.NotBefore = time.Now().Add(-48 * time.Hour)
			c.NotAfter = time.Now().Add(-24 * time.Hour)
		}},
		{name: "future", change: func(c *x509.Certificate) {
			c.NotBefore = time.Now().Add(time.Hour)
			c.NotAfter = time.Now().Add(2 * time.Hour)
		}},
		{name: "client-EKU", change: func(c *x509.Certificate) { c.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth} }},
		{name: "explicit-clock", change: func(c *x509.Certificate) {
			c.NotBefore = time.Now().Add(-48 * time.Hour)
			c.NotAfter = time.Now().Add(-24 * time.Hour)
		}, clock: func() time.Time { return time.Now().Add(-36 * time.Hour) }, valid: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			template := x509.Certificate{SerialNumber: big.NewInt(2), NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour),
				DNSNames: []string{"localhost"}, IPAddresses: []net.IP{net.ParseIP("127.0.0.1")}, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}, KeyUsage: x509.KeyUsageDigitalSignature}
			if tc.change != nil {
				tc.change(&template)
			}
			server := serveCertificate(t, issue(t, &root, template))
			base := http.DefaultTransport.(*http.Transport).Clone()
			base.TLSClientConfig = &tls.Config{Time: tc.clock}
			transport := Select(nil).Wrap(base)
			defer transport.CloseIdleConnections()
			url := server.URL
			if tc.dns {
				url = strings.Replace(url, "127.0.0.1", "localhost", 1)
			}
			response, err := (&http.Client{Transport: transport}).Get(url)
			if response != nil {
				response.Body.Close()
			}
			if (err == nil) != tc.valid {
				t.Fatalf("verification validity=%v; want%v", err == nil, tc.valid)
			}
		})
	}
}

func TestIntermediateRootReplacementCallbacksAndResumption(t *testing.T) {
	clearEnvironment(t)
	root := rootCertificate(t)
	otherRoot := rootCertificate(t)
	intermediate := issue(t, &root, x509.Certificate{SerialNumber: big.NewInt(2), Subject: pkix.Name{CommonName: "owned intermediate"},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign})
	leaf := issue(t, &intermediate, x509.Certificate{SerialNumber: big.NewInt(3), NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour),
		IPAddresses: []net.IP{net.ParseIP("127.0.0.1")}, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}, KeyUsage: x509.KeyUsageDigitalSignature})
	path := filepath.Join(t.TempDir(), "root.pem")
	write := func(c certificate) {
		t.Helper()
		if err := os.WriteFile(path, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: c.der}), 0600); err != nil {
			t.Fatal(err)
		}
	}
	write(root)
	t.Setenv("AWS_CA_BUNDLE", path)
	server := serveCertificate(t, leaf, intermediate.der)
	var peerCalls, connectionCalls, resumed atomic.Int32
	base := http.DefaultTransport.(*http.Transport).Clone()
	base.TLSClientConfig = &tls.Config{MaxVersion: tls.VersionTLS12, ClientSessionCache: tls.NewLRUClientSessionCache(4),
		VerifyPeerCertificate: func(raw [][]byte, chains [][]*x509.Certificate) error {
			peerCalls.Add(1)
			if len(raw) != 2 || len(chains) == 0 {
				return errors.New("unverified chain")
			}
			return nil
		},
		VerifyConnection: func(s tls.ConnectionState) error {
			connectionCalls.Add(1)
			if s.DidResume {
				resumed.Add(1)
			}
			if len(s.VerifiedChains) == 0 {
				return errors.New("unverified state")
			}
			return nil
		}}
	transport := Select(nil).Wrap(base)
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport}
	request := func(url string) error {
		r, e := client.Get(url)
		if r != nil {
			r.Body.Close()
		}
		return e
	}
	if err := request(server.URL); err != nil {
		t.Fatal(err)
	}
	transport.CloseIdleConnections()
	if err := request(server.URL); err != nil {
		t.Fatal(err)
	}
	if resumed.Load() != 1 || connectionCalls.Load() != 2 {
		t.Fatal("resumed handshake was not reverified")
	}
	// Native VerifyPeerCertificate does not run on resumption. Its callback
	// contract is retained separately from VerifyConnection.
	if peerCalls.Load() != 1 {
		t.Fatal("peer callback ran on resumed session")
	}
	write(otherRoot)
	transport.CloseIdleConnections()
	if request(server.URL) == nil {
		t.Fatal("resumption bypassed changed trust roots")
	}
	// Even a base pool which trusts the server must be replaced, not augmented.
	base.TLSClientConfig.RootCAs = x509.NewCertPool()
	base.TLSClientConfig.RootCAs.AddCert(root.cert)
	replaced := Select(nil).Wrap(base)
	defer replaced.CloseIdleConnections()
	r, err := (&http.Client{Transport: replaced}).Get(server.URL)
	if r != nil {
		r.Body.Close()
	}
	if err == nil {
		t.Fatal("configured roots augmented base roots")
	}
	write(root)
	missing := serveCertificate(t, leaf)
	if request(missing.URL) == nil {
		t.Fatal("missing intermediate accepted")
	}
	base.TLSClientConfig.VerifyConnection = func(tls.ConnectionState) error { return errors.New("private callback failure") }
	rejecting := Select(nil).Wrap(base)
	defer rejecting.CloseIdleConnections()
	r, err = (&http.Client{Transport: rejecting}).Get(server.URL)
	if r != nil {
		r.Body.Close()
	}
	if err == nil || strings.Contains(err.Error(), "private callback") {
		t.Fatal("callback rejection lost or exposed")
	}
}
