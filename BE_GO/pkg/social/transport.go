package social

import (
	"crypto/tls"
	"crypto/x509"
	"errors"
	"net/http"
	"os"
)

// RequestsTransport keeps the requests-style CA bundle separate from urllib's
// JWK transport. It owns a clone; callers close its idle connections on shutdown.
// Bundle files are read at construction. Live trust rotation and OpenSSL hashed
// certificate directories require separate compatibility work.
type RequestsTransport struct {
	transport  *http.Transport
	trustError error
}

func NewRequestsTransport(base *http.Transport, caBundle string) *RequestsTransport {
	if base == nil {
		panic("missing provider transport")
	}
	result := &RequestsTransport{transport: base.Clone()}
	if caBundle == "" {
		return result
	}
	// An explicit requests bundle replaces default trust. Never silently fall back
	// to system certificates when the supplied bundle is missing or invalid.
	roots := x509.NewCertPool()
	data, err := os.ReadFile(caBundle)
	if err != nil || !roots.AppendCertsFromPEM(data) {
		result.trustError = errors.New("cannot load provider certificate bundle")
		return result
	}
	if result.transport.TLSClientConfig == nil {
		result.transport.TLSClientConfig = &tls.Config{}
	}
	result.transport.TLSClientConfig.RootCAs = roots
	return result
}

func (t *RequestsTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	if t.trustError != nil && request.URL.Scheme == "https" {
		if request.Body != nil {
			request.Body.Close()
		}
		return nil, t.trustError
	}
	return t.transport.RoundTrip(request)
}

func (t *RequestsTransport) CloseIdleConnections() { t.transport.CloseIdleConnections() }
