package s3trust

import (
	"crypto/tls"
	"crypto/x509"
	"net/http"
	"net/http/httptest"
	"testing"
)

// Django's default storage trust is certifi, not an inherited platform pool.
// An unrelated caller/platform root must not grant trust to the owned client.
func TestAbsentTrustRejectsInheritedPrivateRoot(t *testing.T) {
	clearEnvironment(t)
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Error("private-root request reached the application")
	}))
	defer server.Close()
	private := x509.NewCertPool()
	private.AddCert(server.Certificate())
	base := http.DefaultTransport.(*http.Transport).Clone()
	base.TLSClientConfig = &tls.Config{RootCAs: private}
	transport := Select(nil).Wrap(base)
	defer transport.CloseIdleConnections()
	response, err := (&http.Client{Transport: transport}).Get(server.URL)
	if response != nil {
		response.Body.Close()
	}
	if err == nil {
		t.Fatal("absent storage trust accepted a root outside the pinned Django bundle")
	}
	if base.TLSClientConfig.RootCAs != private {
		t.Fatal("caller transport was mutated")
	}
}

func TestDefaultRootPoolsCannotMutateEachOther(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	defer server.Close()
	first, second := defaultRoots(), defaultRoots()
	if first == nil || second == nil || !first.Equal(second) || first.Equal(x509.NewCertPool()) {
		t.Fatal("default trust must contain the same nonempty pinned roots")
	}
	first.AddCert(server.Certificate())
	if first.Equal(second) || !second.Equal(defaultRoots()) {
		t.Fatal("one caller mutated another caller's default trust")
	}
}
