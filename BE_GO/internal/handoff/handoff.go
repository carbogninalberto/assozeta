// Package handoff replays a request that Go refuses (a refusal.ErrNotPorted
// sentinel) to the Django API and streams Django's response back unchanged.
//
// The middleware is installed first, so it sees the request as received (after
// Caddy stripped /api/v2) and the raw response writer. It records the request
// body up to a limit while the handler reads it. When the handler maps a
// refusal to the stable 500 page, Serve replays the request instead, but only
// when nothing has been written to the response, no effect was recorded for the
// request (database writes, Redis writes, outbound sends; see effects.go), the
// handler did not forbid it (streamed uploads) and the body is complete.
// Otherwise the 500 page stays and the skip is logged.
//
// With an empty URL the middleware is the identity and Serve always reports
// false: the 500 page is answered as before.
package handoff

import (
	"bufio"
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"github.com/labstack/echo/v4"
)

// Header marks a response that Django served for a request Go refused.
const Header = "X-Assozeta-Handoff"

// DefaultMaxBody is the default request body limit for a replay (10 MiB).
const DefaultMaxBody int64 = 10 << 20

// Handoff is the configured Django upstream. A nil *Handoff is disabled.
type Handoff struct {
	base    *url.URL
	maxBody int64
	client  *http.Client
	logger  *slog.Logger
}

// New returns nil for an empty rawURL (handoff disabled). The URL must be an
// absolute http(s) URL without query or fragment, for example http://api:8000.
// maxBody <= 0 means DefaultMaxBody.
func New(rawURL string, maxBody int64, logger *slog.Logger) (*Handoff, error) {
	rawURL = strings.TrimSpace(rawURL)
	if rawURL == "" {
		return nil, nil
	}
	base, err := url.Parse(rawURL)
	if err != nil || (base.Scheme != "http" && base.Scheme != "https") || base.Host == "" || base.RawQuery != "" ||
		base.Fragment != "" || base.User != nil {
		return nil, errors.New("DJANGO_HANDOFF_URL must be an absolute http(s) URL without credentials, query or fragment")
	}
	base.Path = strings.TrimRight(base.Path, "/")
	base.RawPath = ""
	if maxBody <= 0 {
		maxBody = DefaultMaxBody
	}
	if logger == nil {
		logger = slog.Default()
	}
	transport := &http.Transport{
		Proxy:                 nil, // the in-network Django service, never an outbound proxy
		DialContext:           (&net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
		MaxIdleConns:          32,
		MaxIdleConnsPerHost:   32,
		IdleConnTimeout:       90 * time.Second,
		TLSHandshakeTimeout:   10 * time.Second,
		ExpectContinueTimeout: time.Second,
		// Keep Accept-Encoding and the response body exactly as exchanged.
		DisableCompression: true,
	}
	client := &http.Client{Transport: transport, CheckRedirect: func(*http.Request, []*http.Request) error {
		return http.ErrUseLastResponse
	}}
	return &Handoff{base: base, maxBody: maxBody, client: client, logger: logger}, nil
}

// Enabled reports whether refused requests are handed off.
func (h *Handoff) Enabled() bool { return h != nil }

type stateKey struct{}

// state is one request's handoff bookkeeping, shared through the context.
type state struct {
	h       *Handoff
	method  string
	uri     string // the request-target as received: escaped path and raw query
	host    string
	header  http.Header
	body    *bodyRecorder // nil for a request without a body
	writer  *startWriter
	started time.Time
	mu      sync.Mutex
	effect  string // the first recorded effect
	effects int
	// transactions counts the database transactions the request has open.
	transactions int
	forbid       string
	consumed     bool // Serve ran (a request is handed off at most once)
}

func fromContext(ctx context.Context) *state {
	if ctx == nil {
		return nil
	}
	s, _ := ctx.Value(stateKey{}).(*state)
	return s
}

// MarkEffect records that the request had an effect outside the response (a
// database or Redis write, an upload, an outbound send). A later refusal is
// then answered with the 500 page instead of a replay. It is a no-op for
// contexts without handoff (handoff disabled, workers, background jobs).
func MarkEffect(ctx context.Context, kind string) {
	s := fromContext(ctx)
	if s == nil {
		return
	}
	s.mu.Lock()
	if s.effects == 0 {
		s.effect = kind
	}
	s.effects++
	s.mu.Unlock()
}

// Forbid prevents a replay for this request, for example before a handler
// streams the request body to storage.
func Forbid(ctx context.Context, reason string) {
	s := fromContext(ctx)
	if s == nil {
		return
	}
	s.mu.Lock()
	if s.forbid == "" {
		s.forbid = reason
	}
	s.mu.Unlock()
}

// Middleware must be the first Pre middleware. A disabled handoff returns the
// next handler unchanged.
func (h *Handoff) Middleware() echo.MiddlewareFunc {
	return func(next echo.HandlerFunc) echo.HandlerFunc {
		if h == nil {
			return next
		}
		return func(c echo.Context) error {
			r := c.Request()
			uri := r.RequestURI
			if !strings.HasPrefix(uri, "/") {
				uri = r.URL.RequestURI()
			}
			s := &state{h: h, method: r.Method, uri: uri, host: r.Host, header: r.Header.Clone(), started: time.Now()}
			if r.Body != nil && r.Body != http.NoBody {
				s.body = &bodyRecorder{src: r.Body, limit: h.maxBody}
				r.Body = s.body
			}
			s.writer = &startWriter{ResponseWriter: c.Response().Writer}
			c.Response().Writer = s.writer
			c.SetRequest(r.WithContext(context.WithValue(r.Context(), stateKey{}, s)))
			return next(c)
		}
	}
}

// Serve hands a refused request off to Django. It returns true when the
// response was written (Django's response, or a truncated one after an
// upstream failure mid-body); false means the caller answers as before.
// Only errors wrapping refusal.ErrNotPorted are handed off.
func Serve(c echo.Context, err error) bool {
	reason, refused := refusal.Reason(err)
	if !refused {
		return false
	}
	s := fromContext(c.Request().Context())
	if s == nil {
		return false
	}
	s.mu.Lock()
	consumed, effect, effects, forbid, transactions := s.consumed, s.effect, s.effects, s.forbid, s.transactions
	s.consumed = true
	s.mu.Unlock()
	h := s.h
	route := c.Path()
	skip := func(cause string) bool {
		h.logger.Warn("handoff skipped: Go answers the refusal with 500", "method", s.method, "route", route,
			"reason", reason, "cause", cause)
		return false
	}
	switch {
	case consumed:
		return skip("already handed off")
	case s.writer.started || c.Response().Committed:
		return skip("response already started")
	case forbid != "":
		return skip("forbidden: " + forbid)
	case effects > 0:
		return skip(fmt.Sprintf("refusal after %d effect(s), first: %s", effects, effect))
	case transactions > 0:
		return skip("a database transaction of the request is still open")
	}
	if strings.HasPrefix(s.uri, "//") {
		return skip("request-target starts with //")
	}
	var body []byte
	if s.body != nil {
		var ok bool
		if body, ok = s.body.replayable(); !ok {
			return skip("request body not replayable (over the limit or unreadable)")
		}
	}
	response, err := h.forward(c.Request().Context(), s, body)
	if err != nil {
		return skip("upstream error: " + err.Error())
	}
	defer response.Body.Close()
	written, copyErr := s.write(response)
	c.Response().Committed, c.Response().Status, c.Response().Size = true, response.StatusCode, written
	attributes := []any{"method", s.method, "route", route, "status", response.StatusCode, "reason", reason,
		"duration_ms", time.Since(s.started).Milliseconds()}
	if copyErr != nil {
		h.logger.Error("handoff to django: response body interrupted", append(attributes, "error", copyErr.Error())...)
		return true
	}
	h.logger.Info("handoff to django", attributes...)
	return true
}

func (h *Handoff) forward(ctx context.Context, s *state, body []byte) (*http.Response, error) {
	path, query, _ := strings.Cut(s.uri, "?")
	target := &url.URL{Scheme: h.base.Scheme, Host: h.base.Host, Opaque: h.base.Path + path, RawQuery: query}
	if strings.Contains(s.uri, "?") && query == "" {
		target.ForceQuery = true
	}
	var reader io.Reader
	if s.body != nil {
		reader = bytes.NewReader(body)
	}
	out, err := http.NewRequestWithContext(ctx, s.method, h.base.String(), reader)
	if err != nil {
		return nil, err
	}
	out.URL = target
	out.Host = s.host
	out.Header = s.header.Clone()
	removeHopHeaders(out.Header)
	for _, name := range requestOnlyHeaders {
		out.Header.Del(name)
	}
	if s.body != nil {
		out.ContentLength = int64(len(body))
	} else {
		out.ContentLength = 0
		out.Body = nil
	}
	if out.Header.Get("User-Agent") == "" {
		// Go's client would add its own; Django receives what the client sent.
		out.Header["User-Agent"] = nil
	}
	return h.client.Do(out)
}

// write copies Django's status, headers and body to the raw response writer,
// discarding the headers Go's middleware prepared for its own response.
func (s *state) write(response *http.Response) (int64, error) {
	raw := s.writer.ResponseWriter
	header := raw.Header()
	for name := range header {
		delete(header, name)
	}
	for name, values := range response.Header {
		header[name] = append([]string(nil), values...)
	}
	removeHopHeaders(header)
	header.Set(Header, "django")
	s.writer.started = true
	raw.WriteHeader(response.StatusCode)
	controller := http.NewResponseController(raw)
	buffer := make([]byte, 32<<10)
	var written int64
	for {
		n, err := response.Body.Read(buffer)
		if n > 0 {
			m, writeErr := raw.Write(buffer[:n])
			written += int64(m)
			if writeErr != nil {
				return written, writeErr
			}
			_ = controller.Flush()
		}
		if err == io.EOF {
			return written, nil
		}
		if err != nil {
			return written, err
		}
	}
}

// hopHeaders are the per-connection headers a proxy does not forward (RFC 9110
// section 7.6.1), plus the headers named by Connection.
var hopHeaders = []string{"Connection", "Proxy-Connection", "Keep-Alive", "Proxy-Authenticate", "Proxy-Authorization",
	"Te", "Trailer", "Transfer-Encoding", "Upgrade"}

// requestOnlyHeaders were already answered by Go's server (the body is complete).
var requestOnlyHeaders = []string{"Expect"}

func removeHopHeaders(header http.Header) {
	for _, value := range header.Values("Connection") {
		for _, name := range strings.Split(value, ",") {
			if name = strings.TrimSpace(name); name != "" {
				header.Del(name)
			}
		}
	}
	for _, name := range hopHeaders {
		header.Del(name)
	}
}

// bodyRecorder records what the handler reads, up to limit bytes.
type bodyRecorder struct {
	src      io.ReadCloser
	buf      bytes.Buffer
	limit    int64
	overflow bool
	eof      bool
	failed   bool
}

func (b *bodyRecorder) Read(p []byte) (int, error) {
	n, err := b.src.Read(p)
	b.record(p[:n])
	if err == io.EOF {
		b.eof = true
	} else if err != nil {
		b.failed = true
	}
	return n, err
}

func (b *bodyRecorder) record(p []byte) {
	if b.overflow || len(p) == 0 {
		return
	}
	if int64(b.buf.Len())+int64(len(p)) > b.limit {
		b.overflow = true
		b.buf = bytes.Buffer{}
		return
	}
	b.buf.Write(p)
}

// Close leaves the connection's body to the server, which closes it after the
// handler; the unread rest stays available for a replay.
func (b *bodyRecorder) Close() error { return nil }

// replayable reads the unread rest (within the limit) and returns the whole body.
func (b *bodyRecorder) replayable() ([]byte, bool) {
	if b.overflow || b.failed {
		return nil, false
	}
	if !b.eof {
		rest := b.limit - int64(b.buf.Len())
		chunk, err := io.ReadAll(io.LimitReader(b.src, rest+1))
		b.record(chunk)
		if err != nil || b.overflow {
			b.failed = err != nil
			return nil, false
		}
		b.eof = true
	}
	return b.buf.Bytes(), true
}

// startWriter records whether anything reached the client.
type startWriter struct {
	http.ResponseWriter
	started bool
}

func (w *startWriter) WriteHeader(code int) {
	w.started = true
	w.ResponseWriter.WriteHeader(code)
}

func (w *startWriter) Write(p []byte) (int, error) {
	w.started = true
	return w.ResponseWriter.Write(p)
}

func (w *startWriter) Flush() {
	w.started = true
	if flusher, ok := w.ResponseWriter.(http.Flusher); ok {
		flusher.Flush()
	}
}

func (w *startWriter) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	w.started = true
	return http.NewResponseController(w.ResponseWriter).Hijack()
}

func (w *startWriter) Unwrap() http.ResponseWriter { return w.ResponseWriter }
