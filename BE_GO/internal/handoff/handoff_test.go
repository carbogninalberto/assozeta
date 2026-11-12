package handoff

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"github.com/jackc/pgx/v5"
	"github.com/labstack/echo/v4"
	"github.com/redis/go-redis/v9"
)

var errRefused = refusal.New("test input not ported")

const serverError = "go-500"

// upstream records what Django would receive and answers with a fixed response.
type upstream struct {
	mu       sync.Mutex
	requests []recorded
	respond  func(w http.ResponseWriter, r *http.Request)
}

type recorded struct {
	method, uri, host string
	header            http.Header
	body              []byte
	length            int64
}

func (u *upstream) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	u.mu.Lock()
	u.requests = append(u.requests, recorded{r.Method, r.RequestURI, r.Host, r.Header.Clone(), body, r.ContentLength})
	u.mu.Unlock()
	u.respond(w, r)
}

func (u *upstream) calls() []recorded {
	u.mu.Lock()
	defer u.mu.Unlock()
	return append([]recorded(nil), u.requests...)
}

func djangoResponse(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Add("Set-Cookie", "csrftoken=abc; Path=/")
	w.Header().Add("Set-Cookie", "sessionid=def; Path=/; HttpOnly")
	w.Header().Set("Vary", "Accept, Cookie")
	w.Header().Set("Content-Language", "it")
	w.Header().Set("X-Frame-Options", "DENY")
	w.WriteHeader(http.StatusCreated)
	_, _ = io.WriteString(w, `{"served":"django"}`)
}

// app is a Go server: the handoff middleware first, a middleware that prepares
// headers of Go's own response, then the handler.
func app(t *testing.T, h *Handoff, handler func(c echo.Context) error) *httptest.Server {
	t.Helper()
	e := echo.New()
	e.Pre(h.Middleware())
	e.Pre(func(next echo.HandlerFunc) echo.HandlerFunc {
		return func(c echo.Context) error {
			c.Response().Header().Set("Content-Language", "en")
			c.Response().Header().Set("X-Go-Only", "1")
			return next(c)
		}
	})
	e.HTTPErrorHandler = func(err error, c echo.Context) {
		if Serve(c, err) {
			return
		}
		_ = c.String(http.StatusInternalServerError, serverError)
	}
	e.Any("/*", handler)
	server := httptest.NewServer(e)
	t.Cleanup(server.Close)
	return server
}

// refuse mirrors restError: a refusal is handed off, anything else is the 500.
func refuse(c echo.Context, err error) error {
	if Serve(c, err) {
		return nil
	}
	return c.String(http.StatusInternalServerError, serverError)
}

func newHandoff(t *testing.T, target string, maxBody int64) *Handoff {
	t.Helper()
	h, err := New(target, maxBody, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatal(err)
	}
	return h
}

func send(t *testing.T, method, target string, body io.Reader, header http.Header) (*http.Response, []byte) {
	t.Helper()
	request, err := http.NewRequest(method, target, body)
	if err != nil {
		t.Fatal(err)
	}
	for name, values := range header {
		request.Header[name] = values
	}
	response, err := http.DefaultTransport.RoundTrip(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	raw, _ := io.ReadAll(response.Body)
	return response, raw
}

func TestReplayFidelity(t *testing.T) {
	django := &upstream{respond: djangoResponse}
	upstreamServer := httptest.NewServer(django)
	defer upstreamServer.Close()
	h := newHandoff(t, upstreamServer.URL, 0)
	var read []byte
	server := app(t, h, func(c echo.Context) error {
		read, _ = io.ReadAll(c.Request().Body)
		return refuse(c, fmt.Errorf("service: %w", errRefused))
	})
	body := `{"name":"Più","amount":"1.000"}`
	header := http.Header{
		"Authorization": {"Bearer token-value"}, "Cookie": {"sessionid=s1; csrftoken=c1"},
		"X-Group-Id": {"8F14E45F-CEEA-467A-9575-0D7F6A8C1B2E"}, "X-Correlation-Id": {"case-1"},
		"X-Forwarded-For": {"203.0.113.7"}, "Accept-Language": {"it"}, "Content-Type": {"application/json"},
		"Accept-Encoding": {"gzip"}, "Connection": {"X-Hop"}, "X-Hop": {"dropped"},
	}
	response, raw := send(t, http.MethodPatch, server.URL+"/payment/abc%2Fdef/update?a=%ff&b=1&b=2&c=%20", strings.NewReader(body), header)
	if string(read) != body {
		t.Fatalf("handler read %q", read)
	}
	if response.StatusCode != http.StatusCreated || string(raw) != `{"served":"django"}` {
		t.Fatalf("status %d body %q", response.StatusCode, raw)
	}
	if got := response.Header.Values("Set-Cookie"); len(got) != 2 || got[0] != "csrftoken=abc; Path=/" || got[1] != "sessionid=def; Path=/; HttpOnly" {
		t.Fatalf("Set-Cookie %q", got)
	}
	for name, want := range map[string]string{"Content-Type": "application/json", "Vary": "Accept, Cookie", "Content-Language": "it",
		"X-Frame-Options": "DENY", Header: "django", "X-Go-Only": ""} {
		if got := response.Header.Get(name); got != want {
			t.Fatalf("%s = %q, want %q", name, got, want)
		}
	}
	calls := django.calls()
	if len(calls) != 1 {
		t.Fatalf("%d upstream calls", len(calls))
	}
	call := calls[0]
	if call.method != http.MethodPatch || call.uri != "/payment/abc%2Fdef/update?a=%ff&b=1&b=2&c=%20" || string(call.body) != body ||
		call.length != int64(len(body)) {
		t.Fatalf("upstream got %s %s %q (%d)", call.method, call.uri, call.body, call.length)
	}
	if call.host != strings.TrimPrefix(server.URL, "http://") {
		t.Fatalf("Host %q", call.host)
	}
	for name, values := range header {
		if name == "Connection" || name == "X-Hop" {
			if call.header.Get(name) != "" {
				t.Fatalf("hop-by-hop header %s forwarded", name)
			}
			continue
		}
		if got := call.header.Values(name); len(got) != len(values) || got[0] != values[0] {
			t.Fatalf("header %s = %q, want %q", name, got, values)
		}
	}
	if call.header.Get("User-Agent") != "Go-http-client/1.1" {
		// The client's own User-Agent is forwarded, not one added by the handoff.
		t.Fatalf("User-Agent %q", call.header.Get("User-Agent"))
	}
}

func TestUnreadAndPartlyReadBodiesAreReplayedWhole(t *testing.T) {
	django := &upstream{respond: djangoResponse}
	upstreamServer := httptest.NewServer(django)
	defer upstreamServer.Close()
	h := newHandoff(t, upstreamServer.URL, 64)
	for _, readBytes := range []int{0, 5, 40} {
		server := app(t, h, func(c echo.Context) error {
			_, _ = io.ReadFull(c.Request().Body, make([]byte, readBytes))
			return refuse(c, errRefused)
		})
		body := strings.Repeat("x", 40)
		response, _ := send(t, http.MethodPost, server.URL+"/x", strings.NewReader(body), nil)
		calls := django.calls()
		if response.StatusCode != http.StatusCreated || string(calls[len(calls)-1].body) != body {
			t.Fatalf("read %d: status %d body %q", readBytes, response.StatusCode, calls[len(calls)-1].body)
		}
	}
}

func TestBodyOverLimitIsNotHandedOff(t *testing.T) {
	django := &upstream{respond: djangoResponse}
	upstreamServer := httptest.NewServer(django)
	defer upstreamServer.Close()
	h := newHandoff(t, upstreamServer.URL, 16)
	for _, readAll := range []bool{true, false} {
		server := app(t, h, func(c echo.Context) error {
			if readAll {
				_, _ = io.ReadAll(c.Request().Body)
			}
			return refuse(c, errRefused)
		})
		response, raw := send(t, http.MethodPost, server.URL+"/x", strings.NewReader(strings.Repeat("y", 17)), nil)
		if response.StatusCode != 500 || string(raw) != serverError || response.Header.Get(Header) != "" {
			t.Fatalf("readAll %v: status %d", readAll, response.StatusCode)
		}
	}
	if len(django.calls()) != 0 {
		t.Fatal("over-limit body reached Django")
	}
}

// A body the handler streamed to storage (the backup upload) is never replayed.
func TestStreamedUploadIsNotHandedOff(t *testing.T) {
	django := &upstream{respond: djangoResponse}
	upstreamServer := httptest.NewServer(django)
	defer upstreamServer.Close()
	h := newHandoff(t, upstreamServer.URL, 0)
	server := app(t, h, func(c echo.Context) error {
		Forbid(c.Request().Context(), "streamed backup upload")
		_, _ = io.Copy(io.Discard, c.Request().Body)
		return refuse(c, errRefused)
	})
	response, _ := send(t, http.MethodPost, server.URL+"/instance/admin/backups/upload", strings.NewReader("archive"), nil)
	if response.StatusCode != 500 || len(django.calls()) != 0 {
		t.Fatalf("status %d, %d upstream calls", response.StatusCode, len(django.calls()))
	}
}

func TestRefusalAfterEffectIsNotHandedOff(t *testing.T) {
	django := &upstream{respond: djangoResponse}
	upstreamServer := httptest.NewServer(django)
	defer upstreamServer.Close()
	h := newHandoff(t, upstreamServer.URL, 0)
	effects := map[string]func(ctx context.Context){
		"mark": func(ctx context.Context) { MarkEffect(ctx, "object storage write") },
		"sql": func(ctx context.Context) {
			QueryTracer{}.TraceQueryStart(ctx, nil, pgxStart("UPDATE application_payment SET paid = true WHERE id = $1"))
		},
		"redis": func(ctx context.Context) { _ = RedisHook{}.ProcessHook(noRedis)(ctx, redisCmd("lpush")) },
	}
	for name, effect := range effects {
		server := app(t, h, func(c echo.Context) error {
			effect(c.Request().Context())
			return refuse(c, errRefused)
		})
		response, _ := send(t, http.MethodPost, server.URL+"/x", strings.NewReader("{}"), nil)
		if response.StatusCode != 500 {
			t.Fatalf("%s: status %d", name, response.StatusCode)
		}
	}
	reads := map[string]func(ctx context.Context){
		"sql": func(ctx context.Context) {
			QueryTracer{}.TraceQueryStart(ctx, nil, pgxStart("SELECT 1 FROM application_payment FOR UPDATE"))
		},
		"redis": func(ctx context.Context) { _ = RedisHook{}.ProcessHook(noRedis)(ctx, redisCmd("get")) },
	}
	for name, read := range reads {
		server := app(t, h, func(c echo.Context) error {
			read(c.Request().Context())
			return refuse(c, errRefused)
		})
		response, _ := send(t, http.MethodGet, server.URL+"/x", nil, nil)
		if response.StatusCode != http.StatusCreated {
			t.Fatalf("read %s: status %d", name, response.StatusCode)
		}
	}
	if len(django.calls()) != len(reads) {
		t.Fatalf("%d upstream calls", len(django.calls()))
	}
}

func TestStartedResponseIsNotHandedOff(t *testing.T) {
	django := &upstream{respond: djangoResponse}
	upstreamServer := httptest.NewServer(django)
	defer upstreamServer.Close()
	h := newHandoff(t, upstreamServer.URL, 0)
	server := app(t, h, func(c echo.Context) error {
		c.Response().WriteHeader(http.StatusOK)
		_, _ = c.Response().Write([]byte("partial"))
		if Serve(c, errRefused) {
			t.Error("handed off after the response started")
		}
		return nil
	})
	response, raw := send(t, http.MethodGet, server.URL+"/x", nil, nil)
	if response.StatusCode != 200 || string(raw) != "partial" || len(django.calls()) != 0 {
		t.Fatalf("status %d body %q", response.StatusCode, raw)
	}
}

func TestOnlyRefusalsAreHandedOff(t *testing.T) {
	django := &upstream{respond: djangoResponse}
	upstreamServer := httptest.NewServer(django)
	defer upstreamServer.Close()
	h := newHandoff(t, upstreamServer.URL, 0)
	server := app(t, h, func(c echo.Context) error { return refuse(c, errors.New("database unavailable")) })
	response, _ := send(t, http.MethodGet, server.URL+"/x", nil, nil)
	if response.StatusCode != 500 || len(django.calls()) != 0 {
		t.Fatalf("status %d", response.StatusCode)
	}
	// A refusal returned to Echo reaches the error handler and is handed off too.
	server = app(t, h, func(c echo.Context) error { return fmt.Errorf("wrapped: %w", errRefused) })
	response, _ = send(t, http.MethodGet, server.URL+"/y", nil, nil)
	if response.StatusCode != http.StatusCreated || response.Header.Get(Header) != "django" {
		t.Fatalf("returned refusal: status %d", response.StatusCode)
	}
}

func TestDisabledKeepsThe500(t *testing.T) {
	h := newHandoff(t, "", 0)
	if h != nil || h.Enabled() {
		t.Fatal("empty URL must disable the handoff")
	}
	server := app(t, h, func(c echo.Context) error {
		_, _ = io.ReadAll(c.Request().Body)
		return refuse(c, errRefused)
	})
	response, raw := send(t, http.MethodPost, server.URL+"/x", strings.NewReader("{}"), nil)
	if response.StatusCode != 500 || string(raw) != serverError || response.Header.Get(Header) != "" {
		t.Fatalf("status %d body %q", response.StatusCode, raw)
	}
}

func TestUpstreamErrors(t *testing.T) {
	// Unreachable: the 500 stays.
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	closed := "http://" + listener.Addr().String()
	listener.Close()
	server := app(t, newHandoff(t, closed, 0), func(c echo.Context) error { return refuse(c, errRefused) })
	response, raw := send(t, http.MethodGet, server.URL+"/x", nil, nil)
	if response.StatusCode != 500 || string(raw) != serverError {
		t.Fatalf("unreachable: status %d body %q", response.StatusCode, raw)
	}
	// Django's own errors and redirects are passed through unchanged.
	for _, status := range []int{http.StatusInternalServerError, http.StatusBadGateway, http.StatusFound, http.StatusNotModified} {
		django := &upstream{respond: func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Location", "/elsewhere")
			w.WriteHeader(status)
			if status != http.StatusNotModified {
				_, _ = io.WriteString(w, "django says")
			}
		}}
		upstreamServer := httptest.NewServer(django)
		server := app(t, newHandoff(t, upstreamServer.URL, 0), func(c echo.Context) error { return refuse(c, errRefused) })
		response, raw := send(t, http.MethodGet, server.URL+"/x", nil, nil)
		upstreamServer.Close()
		want := "django says"
		if status == http.StatusNotModified {
			want = ""
		}
		if response.StatusCode != status || string(raw) != want || response.Header.Get("Location") != "/elsewhere" ||
			response.Header.Get(Header) != "django" {
			t.Fatalf("status %d: got %d %q", status, response.StatusCode, raw)
		}
	}
	// Interrupted mid-body: the response is already Django's, so it is cut short.
	django := &upstream{respond: func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Length", "100")
		w.WriteHeader(http.StatusOK)
		_, _ = io.WriteString(w, "half")
		if hijacker, ok := w.(http.Hijacker); ok {
			conn, _, _ := hijacker.Hijack()
			conn.Close()
		}
	}}
	upstreamServer := httptest.NewServer(django)
	defer upstreamServer.Close()
	server = app(t, newHandoff(t, upstreamServer.URL, 0), func(c echo.Context) error {
		if !Serve(c, errRefused) {
			t.Error("a started Django response must be reported as written")
		}
		return nil
	})
	request, _ := http.NewRequest(http.MethodGet, server.URL+"/x", nil)
	if response, err := http.DefaultTransport.RoundTrip(request); err == nil {
		raw, readErr := io.ReadAll(response.Body)
		response.Body.Close()
		if response.StatusCode != 200 || readErr == nil || !bytes.HasPrefix([]byte("half"), raw) {
			t.Fatalf("interrupted: status %d body %q err %v", response.StatusCode, raw, readErr)
		}
	}
}

func TestNewRejectsInvalidURLs(t *testing.T) {
	for _, value := range []string{"api:8000", "ftp://api", "http://name@api:8000", "http://api:8000/?x=1", "http:///x"} {
		if _, err := New(value, 0, nil); err == nil {
			t.Fatalf("%q accepted", value)
		}
	}
	h, err := New(" http://api:8000/prefix/ ", 0, nil)
	if err != nil || h.base.Path != "/prefix" || h.maxBody != DefaultMaxBody {
		t.Fatalf("%v %+v", err, h)
	}
}

func TestBasePathPrefixesTheRequestTarget(t *testing.T) {
	django := &upstream{respond: djangoResponse}
	upstreamServer := httptest.NewServer(django)
	defer upstreamServer.Close()
	server := app(t, newHandoff(t, upstreamServer.URL+"/django/", 0), func(c echo.Context) error { return refuse(c, errRefused) })
	send(t, http.MethodGet, server.URL+"/course/list?ordering=title__lower", nil, nil)
	if calls := django.calls(); len(calls) != 1 || calls[0].uri != "/django/course/list?ordering=title__lower" {
		t.Fatalf("%+v", calls)
	}
}

func TestSQLWrites(t *testing.T) {
	for sql, want := range map[string]bool{
		"SELECT * FROM t":                                false,
		"  -- comment\n/* x */ (SELECT 1)":               false,
		"select 1 for update skip locked":                false,
		"begin isolation level read committed":           false,
		"commit":                                         false,
		"SAVEPOINT s1":                                   false,
		"SET LOCAL statement_timeout = 5":                false,
		"WITH x AS (SELECT 1) SELECT * FROM x":           false,
		"INSERT INTO t VALUES (1)":                       true,
		"update t set a = 1":                             true,
		"DELETE FROM t":                                  true,
		"WITH d AS (DELETE FROM t RETURNING *) SELECT 1": true,
		"WITH u AS (UPDATE t SET a = 1) SELECT 1":        true,
		"SELECT nextval('seq')":                          true,
		"TRUNCATE t":                                     true,
		"COPY t FROM STDIN":                              true,
		"CALL proc()":                                    true,
		"":                                               true,
	} {
		if got := SQLWrites(sql); got != want {
			t.Errorf("SQLWrites(%q) = %v, want %v", sql, got, want)
		}
	}
}

func TestRefusalSentinels(t *testing.T) {
	wrapped := fmt.Errorf("handler: %w", errRefused)
	if !errors.Is(wrapped, refusal.ErrNotPorted) || !errors.Is(wrapped, errRefused) {
		t.Fatal("a wrapped refusal must match both its sentinel and ErrNotPorted")
	}
	other := refusal.New("other input not ported")
	if errors.Is(wrapped, other) || errors.Is(errors.New("x"), refusal.ErrNotPorted) {
		t.Fatal("sentinels must stay distinct")
	}
	if reason, ok := refusal.Reason(wrapped); !ok || reason != "test input not ported" {
		t.Fatalf("reason %q %v", reason, ok)
	}
}

func pgxStart(sql string) pgx.TraceQueryStartData { return pgx.TraceQueryStartData{SQL: sql} }

func noRedis(context.Context, redis.Cmder) error { return nil }

func redisCmd(name string) redis.Cmder { return redis.NewCmd(context.Background(), name, "key") }

// Django's replay would wait on the row locks of a transaction Go still holds.
func TestOpenTransactionIsNotHandedOff(t *testing.T) {
	django := &upstream{respond: djangoResponse}
	upstreamServer := httptest.NewServer(django)
	defer upstreamServer.Close()
	h := newHandoff(t, upstreamServer.URL, 0)
	for _, statements := range [][]string{
		{"begin", "SELECT 1 FROM t FOR UPDATE"},
		{"begin", "savepoint s", "rollback to savepoint s"},
	} {
		server := app(t, h, func(c echo.Context) error {
			for _, sql := range statements {
				QueryTracer{}.TraceQueryStart(c.Request().Context(), nil, pgxStart(sql))
			}
			return refuse(c, errRefused)
		})
		if response, _ := send(t, http.MethodGet, server.URL+"/x", nil, nil); response.StatusCode != 500 {
			t.Fatalf("%q: status %d", statements, response.StatusCode)
		}
	}
	server := app(t, h, func(c echo.Context) error {
		for _, sql := range []string{"begin", "SELECT 1 FROM t FOR UPDATE", "rollback"} {
			QueryTracer{}.TraceQueryStart(c.Request().Context(), nil, pgxStart(sql))
		}
		return refuse(c, errRefused)
	})
	if response, _ := send(t, http.MethodGet, server.URL+"/x", nil, nil); response.StatusCode != http.StatusCreated {
		t.Fatalf("closed transaction: status %d", response.StatusCode)
	}
	if len(django.calls()) != 1 {
		t.Fatalf("%d upstream calls", len(django.calls()))
	}
}
