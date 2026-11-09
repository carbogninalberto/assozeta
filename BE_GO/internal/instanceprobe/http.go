// Package instanceprobe holds the outbound side of the owner-only instance
// operations in BE/instance: the GitHub release feed read by
// release_catalog.read_json (urllib), the integration checks of
// integration_probe.get_json (requests 2.34.2 / urllib3 2.8.0) and the SMTP
// connection test of diagnostic_probe (Django's SMTP EmailBackend).
//
// Requests go through the process environment's HTTPS_PROXY/HTTP_PROXY/
// NO_PROXY like urllib and requests do. Certificates are verified against
// the system roots, or only the PEM file named by SSL_CERT_FILE when it is
// set (OpenSSL's default verify paths; the Python processes read the same
// variable, requests through REQUESTS_CA_BUNDLE). HTTP/1.1 only.
package instanceprobe

import (
	"bytes"
	"compress/flate"
	"compress/gzip"
	"compress/zlib"
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

// RequestsVersion is requests.__version__ (its default User-Agent).
const RequestsVersion = "2.34.2"

// Client sends the outbound requests. RootCAs nil means the system roots.
type Client struct {
	RootCAs *x509.CertPool
	Proxy   func(*http.Request) (*url.URL, error)
}

// NewClient reads SSL_CERT_FILE (when set, the only trusted roots).
func NewClient() (*Client, error) {
	c := &Client{Proxy: http.ProxyFromEnvironment}
	if path := os.Getenv("SSL_CERT_FILE"); path != "" {
		pem, err := os.ReadFile(path)
		if err != nil {
			return nil, err
		}
		pool := x509.NewCertPool()
		if !pool.AppendCertsFromPEM(pem) {
			return nil, errors.New("instanceprobe: SSL_CERT_FILE holds no certificate")
		}
		c.RootCAs = pool
	}
	return c, nil
}

// TLSConfig is ssl.create_default_context() for host: verified chain and
// host name, TLS 1.2 or later.
func (c *Client) TLSConfig(host string) *tls.Config {
	return &tls.Config{ServerName: host, RootCAs: c.RootCAs, MinVersion: tls.VersionTLS12, NextProtos: []string{"http/1.1"}}
}

// readTimeoutConn applies a Python socket timeout: each read and write has
// its own deadline.
type readTimeoutConn struct {
	net.Conn
	timeout time.Duration
}

func (c *readTimeoutConn) Read(b []byte) (int, error) {
	_ = c.Conn.SetReadDeadline(time.Now().Add(c.timeout))
	return c.Conn.Read(b)
}

func (c *readTimeoutConn) Write(b []byte) (int, error) {
	_ = c.Conn.SetWriteDeadline(time.Now().Add(c.timeout))
	return c.Conn.Write(b)
}

func (c *Client) transport(connect, read time.Duration, keepAlive bool) *http.Transport {
	dialer := &net.Dialer{Timeout: connect}
	proxy := c.Proxy
	if proxy == nil {
		proxy = http.ProxyFromEnvironment
	}
	return &http.Transport{
		Proxy: proxy,
		DialContext: func(ctx context.Context, network, address string) (net.Conn, error) {
			conn, err := dialer.DialContext(ctx, network, address)
			if err != nil {
				return nil, err
			}
			return &readTimeoutConn{Conn: conn, timeout: read}, nil
		},
		TLSClientConfig:        &tls.Config{RootCAs: c.RootCAs, MinVersion: tls.VersionTLS12, NextProtos: []string{"http/1.1"}},
		TLSHandshakeTimeout:    connect + read,
		TLSNextProto:           map[string]func(string, *tls.Conn) http.RoundTripper{},
		DisableCompression:     true,
		DisableKeepAlives:      !keepAlive,
		MaxResponseHeaderBytes: 64 << 10,
	}
}

// HTTPError is a final response urllib raises as HTTPError.
type HTTPError struct{ Code int }

func (e *HTTPError) Error() string { return "instanceprobe: HTTP " + http.StatusText(e.Code) }

// ErrUnavailable is URLError/TimeoutError/OSError (or a ValueError from
// json.load) inside read_json.
var ErrUnavailable = errors.New("instanceprobe: service unavailable")

// ErrNotPorted marks a response this port does not reproduce (it answers
// with the server error page): a body shorter than its Content-Length
// (http.client.IncompleteRead), an encoding json.loads would read as UTF-16
// or UTF-32, or a redirect target urllib would rewrite.
var ErrNotPorted = errors.New("instanceprobe: outbound response not ported")

// ReadJSON is release_catalog.read_json(url) on urllib.request.urlopen with
// a 15 s timeout: the GitHub headers plus urllib's Accept-Encoding: identity
// and Connection: close, redirects followed like HTTPRedirectHandler (at
// most 10, then the redirect's HTTPError), any other non-2xx status an
// HTTPError, and json.load of the whole body.
func (c *Client) ReadJSON(ctx context.Context, target string, headers map[string]string) (any, error) {
	client := &http.Client{Transport: c.transport(15*time.Second, 15*time.Second, false),
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	visited := map[string]int{}
	for redirects := 0; ; redirects++ {
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
		if err != nil {
			return nil, ErrUnavailable
		}
		request.Header.Set("Accept-Encoding", "identity")
		for key, value := range headers {
			request.Header.Set(key, value)
		}
		request.Header.Set("Connection", "close")
		request.Close = true
		response, err := client.Do(request)
		if err != nil {
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			return nil, ErrUnavailable
		}
		body, readErr := io.ReadAll(response.Body)
		response.Body.Close()
		switch response.StatusCode {
		case 301, 302, 303, 307, 308:
			location := response.Header.Get("Location")
			if location == "" {
				location = response.Header.Get("Uri")
			}
			if location == "" {
				return nil, &HTTPError{Code: response.StatusCode}
			}
			next, err := redirectTarget(target, location)
			if err != nil {
				return nil, err
			}
			if next == "" {
				return nil, &HTTPError{Code: response.StatusCode}
			}
			if visited[next] >= 4 || len(visited) >= 10 {
				return nil, &HTTPError{Code: response.StatusCode}
			}
			visited[next]++
			target = next
			continue
		}
		if response.StatusCode < 200 || response.StatusCode > 299 {
			return nil, &HTTPError{Code: response.StatusCode}
		}
		if readErr != nil {
			if errors.Is(readErr, io.ErrUnexpectedEOF) {
				return nil, ErrNotPorted
			}
			return nil, ErrUnavailable
		}
		value, err := LoadJSON(body)
		switch {
		case errors.Is(err, ErrJSONEncoding):
			return nil, ErrNotPorted
		case err != nil:
			return nil, ErrUnavailable
		}
		return value, nil
	}
}

// redirectTarget is HTTPRedirectHandler.redirect_request's URL handling for
// a Location that needs no quoting: urljoin, then only http, https and ftp
// (anything else: "" for the redirect's HTTPError).
func redirectTarget(base, location string) (string, error) {
	for _, r := range location {
		if r <= ' ' || r >= 0x7f || strings.ContainsRune(`"<>\^`+"`{|}", r) {
			return "", ErrNotPorted
		}
	}
	from, err := url.Parse(base)
	if err != nil {
		return "", ErrNotPorted
	}
	to, err := url.Parse(location)
	if err != nil {
		return "", ErrNotPorted
	}
	next := from.ResolveReference(to)
	switch next.Scheme {
	case "http", "https":
	case "ftp":
		return "", ErrNotPorted
	default:
		return "", nil
	}
	if next.Path == "" && next.Host != "" {
		next.Path = "/"
	}
	next.Fragment = ""
	return next.String(), nil
}

// Response is the part of a requests response get_json reads.
type Response struct {
	Status int
	// Body is response.raw.read(limit+1, decode_content=True); only read for
	// a 200.
	Body []byte
}

// Get is integration_probe.get_json's requests.get(url, headers=headers,
// timeout=(3, 4), allow_redirects=False, stream=True): requests' default
// headers, no redirect, and for a 200 up to limit+1 bytes of the body
// decoded like urllib3 (gzip and deflate; other codings left as sent).
// Every failure is an error (the probe's generic failure).
func (c *Client) Get(ctx context.Context, target string, headers map[string]string, limit int) (*Response, error) {
	// requests keeps the connection alive (Connection: keep-alive); the pool
	// ends with the probe.
	transport := c.transport(3*time.Second, 4*time.Second, true)
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
	if err != nil {
		return nil, err
	}
	request.Header.Set("User-Agent", "python-requests/"+RequestsVersion)
	request.Header.Set("Accept-Encoding", "gzip, deflate")
	request.Header.Set("Accept", "*/*")
	request.Header.Set("Connection", "keep-alive")
	for key, value := range headers {
		request.Header.Set(key, value)
	}
	response, err := client.Do(request)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	result := &Response{Status: response.StatusCode}
	if response.StatusCode != 200 {
		return result, nil
	}
	reader, err := decodedBody(response)
	if err != nil {
		return nil, err
	}
	if result.Body, err = io.ReadAll(io.LimitReader(reader, int64(limit)+1)); err != nil {
		return nil, err
	}
	return result, nil
}

// decodedBody follows urllib3's content decoders: each listed coding,
// gzip/x-gzip (concatenated members) and deflate (zlib, else raw), applied
// in reverse; unknown codings stop decoding and pass the bytes through.
func decodedBody(response *http.Response) (io.Reader, error) {
	var codings []string
	for _, item := range strings.Split(strings.ToLower(response.Header.Get("Content-Encoding")), ",") {
		if item = strings.TrimSpace(item); item != "" {
			codings = append(codings, item)
		}
	}
	for _, coding := range codings {
		if coding != "gzip" && coding != "x-gzip" && coding != "deflate" {
			return response.Body, nil
		}
	}
	var reader io.Reader = response.Body
	for i := len(codings) - 1; i >= 0; i-- {
		switch codings[i] {
		case "gzip", "x-gzip":
			gz, err := gzip.NewReader(reader)
			if err != nil {
				return nil, err
			}
			reader = gz
		case "deflate":
			raw, err := io.ReadAll(reader)
			if err != nil {
				return nil, err
			}
			if z, err := zlib.NewReader(bytes.NewReader(raw)); err == nil {
				reader = z
			} else {
				reader = flate.NewReader(bytes.NewReader(raw))
			}
		}
	}
	return reader, nil
}
