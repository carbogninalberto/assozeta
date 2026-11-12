package social

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/pem"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
)

func TestRequestsCABundleVerifiesPrivateProvider(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1); w.Write([]byte("provider")) }))
	defer server.Close()
	path := filepath.Join(t.TempDir(), "ca.pem")
	if err := os.WriteFile(path, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: server.Certificate().Raw}), 0600); err != nil {
		t.Fatal(err)
	}
	base := http.DefaultTransport.(*http.Transport).Clone()
	transport := NewRequestsTransport(base, path)
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport}
	response, err := client.Get(server.URL)
	if err != nil {
		t.Fatal("configured CA was not trusted:", err)
	}
	data, err := io.ReadAll(response.Body)
	response.Body.Close()
	if err != nil || string(data) != "provider" || calls.Load() != 1 {
		t.Fatal("provider response differs")
	}
	if base.TLSClientConfig != nil && base.TLSClientConfig.RootCAs != nil {
		t.Fatal("shared transport mutated")
	}
	// A trusted root does not disable hostname validation.
	transport.transport.TLSClientConfig.ServerName = "different.example.test"
	transport.CloseIdleConnections()
	if response, err = client.Get(server.URL); err == nil {
		response.Body.Close()
		t.Fatal("hostname mismatch accepted")
	}
}

func TestRequestsCABundleFailureIsDeferredAndDoesNotUseDefaultTrust(t *testing.T) {
	for _, kind := range []string{"missing", "empty", "malformed", "unrelated"} {
		t.Run(kind, func(t *testing.T) {
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { t.Error("untrusted provider reached") }))
			defer server.Close()
			path := filepath.Join(t.TempDir(), "private-config-path")
			if kind != "missing" {
				data := []byte("")
				if kind == "malformed" {
					data = []byte("not a CA")
				}
				if kind == "unrelated" {
					key, err := rsa.GenerateKey(rand.Reader, 2048)
					if err != nil {
						t.Fatal(err)
					}
					template := *server.Certificate()
					template.PublicKey = &key.PublicKey
					cert, err := x509.CreateCertificate(rand.Reader, &template, &template, &key.PublicKey, key)
					if err != nil {
						t.Fatal(err)
					}
					data = pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: cert})
				}
				if err := os.WriteFile(path, data, 0600); err != nil {
					t.Fatal(err)
				}
			}
			// The base already trusts this endpoint; an explicit bundle must replace it.
			base := server.Client().Transport.(*http.Transport)
			transport := NewRequestsTransport(base, path)
			defer transport.CloseIdleConnections()
			client := &http.Client{Transport: transport}
			if response, err := client.Get(server.URL); err == nil {
				response.Body.Close()
				t.Fatal("invalid explicit CA fell back to base trust")
			}
		})
	}
}
