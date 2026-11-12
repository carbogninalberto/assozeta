// Package googleapi is the part of google-auth 2.38.0, google-auth-oauthlib
// 1.2.1 (requests-oauthlib 2.0.0, oauthlib 4.0.0), google-auth-httplib2 0.2.0
// and google-api-python-client 2.164.0 that BE/application/views/google_views.py
// and User.get_google_credentials use: the client secrets Flow (authorization
// URL and code exchange), authorized-user Credentials (load, expiry, refresh,
// to_json) and the Calendar v3 calls, with each library's exact request line,
// ordered headers and body (python-requests 2.34.2, httplib2 0.32.0, Python
// 3.13.16 as in BE/Dockerfile).
package googleapi

import (
	"bufio"
	"bytes"
	"compress/flate"
	"compress/gzip"
	"compress/zlib"
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/handoff"
)

const (
	// OAuth2Host and APIHost are the Google origins the libraries address.
	OAuth2Host = "https://oauth2.googleapis.com"
	APIHost    = "https://www.googleapis.com"
	// TokenURI is google.oauth2.credentials._GOOGLE_OAUTH2_TOKEN_ENDPOINT
	// (from_authorized_user_info always uses it); RevokeURI is the view's literal.
	TokenURI  = OAuth2Host + "/token"
	RevokeURI = OAuth2Host + "/revoke"

	pythonVersion = "3.13.16"
	// The x-goog-api-client values and user agents of the pinned libraries.
	apiClientHeader  = "gdcl/2.164.0 gl-python/" + pythonVersion + " cred-type/u"
	authMetricHeader = "gl-python/" + pythonVersion + " auth/2.38.0 cred-type/u"
	requestsAgent    = "python-requests/2.34.2"
	httplib2Agent    = "Python-httplib2/0.32.0 (gzip)"
	// requestTimeout is google.auth.transport.requests' default (120 s); httplib2
	// and requests.post have none, bounded here by the same value.
	requestTimeout = 120 * time.Second
)

// Header is one request header in the order the Python libraries send it.
type Header struct{ Name, Value string }

// Response is a raw HTTP answer (body decoded per Content-Encoding).
type Response struct {
	Status int
	Reason string
	Body   []byte
}

// ConnectionError is a transport failure (requests' ConnectionError,
// httplib2's socket errors): an exception whose text never names a quota.
type ConnectionError struct{ err error }

func (e *ConnectionError) Error() string          { return "google: connection error: " + e.err.Error() }
func (e *ConnectionError) Mentions(_ string) bool { return false }

// Transport writes each request byte for byte (net/http would add or drop
// headers such as Content-Length on GET), one connection per request.
// Requests addressed to OAuth2Host and APIHost go to the configured bases
// (GOOGLE_OAUTH2_BASE / GOOGLE_API_BASE, used by the parity harness's stub;
// unset in production).
type Transport struct {
	overrides map[string]string
	sleep     func(context.Context, time.Duration) error
	random    func() float64
}

func NewTransport(oauth2Base, apiBase string, random func() float64) *Transport {
	overrides := map[string]string{}
	if oauth2Base != "" {
		overrides[OAuth2Host] = strings.TrimRight(oauth2Base, "/")
	}
	if apiBase != "" {
		overrides[APIHost] = strings.TrimRight(apiBase, "/")
	}
	return &Transport{overrides: overrides, sleep: sleepContext, random: random}
}

func sleepContext(ctx context.Context, d time.Duration) error {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func (t *Transport) address(target string) string {
	for origin, base := range t.overrides {
		if strings.HasPrefix(target, origin+"/") || target == origin {
			return base + target[len(origin):]
		}
	}
	return target
}

// Do sends method target with the headers (after Host) and body.
func (t *Transport) Do(ctx context.Context, method, target string, headers []Header, body []byte) (*Response, error) {
	if method != "GET" && method != "HEAD" {
		handoff.MarkEffect(ctx, "google request")
	}
	address := t.address(target)
	scheme, rest, ok := strings.Cut(address, "://")
	if !ok || (scheme != "http" && scheme != "https") {
		return nil, &ConnectionError{err: fmt.Errorf("unsupported URL %q", address)}
	}
	authority, path := rest, "/"
	if index := strings.IndexAny(rest, "/?"); index >= 0 {
		authority, path = rest[:index], rest[index:]
		if path[0] == '?' {
			path = "/" + path
		}
	}
	if index := strings.IndexByte(path, '#'); index >= 0 {
		path = path[:index]
	}
	hostname, port := authority, map[string]string{"http": "80", "https": "443"}[scheme]
	hostHeader := authority
	if h, p, err := net.SplitHostPort(authority); err == nil {
		hostname, port = h, p
		if (scheme == "http" && p == "80") || (scheme == "https" && p == "443") {
			hostHeader = h
		}
	}
	deadline := time.Now().Add(requestTimeout)
	if d, ok := ctx.Deadline(); ok && d.Before(deadline) {
		deadline = d
	}
	dialer := &net.Dialer{Deadline: deadline}
	var conn net.Conn
	var err error
	if scheme == "https" {
		conn, err = tls.DialWithDialer(dialer, "tcp", net.JoinHostPort(hostname, port), &tls.Config{ServerName: hostname})
	} else {
		conn, err = dialer.DialContext(ctx, "tcp", net.JoinHostPort(hostname, port))
	}
	if err != nil {
		return nil, &ConnectionError{err: err}
	}
	defer conn.Close()
	_ = conn.SetDeadline(deadline)
	stop := context.AfterFunc(ctx, func() { _ = conn.SetDeadline(time.Unix(1, 0)) })
	defer stop()
	var out bytes.Buffer
	out.WriteString(method + " " + path + " HTTP/1.1\r\nHost: " + hostHeader + "\r\n")
	for _, header := range headers {
		out.WriteString(header.Name + ": " + header.Value + "\r\n")
	}
	out.WriteString("\r\n")
	out.Write(body)
	if _, err := conn.Write(out.Bytes()); err != nil {
		return nil, &ConnectionError{err: err}
	}
	answer, err := http.ReadResponse(bufio.NewReader(conn), &http.Request{Method: method})
	if err != nil {
		return nil, &ConnectionError{err: err}
	}
	defer answer.Body.Close()
	data, err := io.ReadAll(answer.Body)
	if err != nil {
		return nil, &ConnectionError{err: err}
	}
	if data, err = decodeContent(answer.Header.Get("Content-Encoding"), data); err != nil {
		return nil, &ConnectionError{err: err}
	}
	reason := strings.TrimSpace(strings.TrimPrefix(answer.Status, strconv.Itoa(answer.StatusCode)))
	return &Response{Status: answer.StatusCode, Reason: reason, Body: data}, nil
}

// decodeContent is the gzip/deflate decoding both requests (urllib3) and httplib2 apply.
func decodeContent(encoding string, data []byte) ([]byte, error) {
	switch strings.ToLower(strings.TrimSpace(encoding)) {
	case "gzip", "x-gzip":
		reader, err := gzip.NewReader(bytes.NewReader(data))
		if err != nil {
			return nil, err
		}
		return io.ReadAll(reader)
	case "deflate":
		if reader, err := zlib.NewReader(bytes.NewReader(data)); err == nil {
			if decoded, err := io.ReadAll(reader); err == nil {
				return decoded, nil
			}
		}
		return io.ReadAll(flate.NewReader(bytes.NewReader(data)))
	}
	return data, nil
}

var errNoTransport = errors.New("google transport not configured")

// requestsHeaders is a requests.Session request: the session defaults, then
// the request's own headers (a request header replaces a default in place).
func requestsHeaders(accept string, extra []Header, length int, trailing ...Header) []Header {
	headers := []Header{{"User-Agent", requestsAgent}, {"Accept-Encoding", "gzip, deflate"}, {"Accept", accept}, {"Connection", "keep-alive"}}
	headers = append(headers, extra...)
	headers = append(headers, Header{"Content-Length", strconv.Itoa(length)})
	return append(headers, trailing...)
}
