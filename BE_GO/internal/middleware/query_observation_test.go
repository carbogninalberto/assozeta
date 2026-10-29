package middleware

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/queryobservation"
	"github.com/jackc/pgx/v5"
	"github.com/labstack/echo/v4"
)

func TestQueryObservationHeaderContextAndResponse(t *testing.T) {
	var sink bytes.Buffer
	o, err := queryobservation.New(&sink, "11111111-2222-4333-8444-555555555555")
	if err != nil {
		t.Fatal(err)
	}
	e := echo.New()
	e.Pre(QueryObservation(o))
	var ids []string
	e.Use(func(next echo.HandlerFunc) echo.HandlerFunc {
		return func(c echo.Context) error {
			id := c.Response().Header().Get("X-Query-Observation-ID")
			if id == "" || id == "HEADER_SECRET" {
				t.Fatal("observation did not wrap inner middleware")
			}
			ids = append(ids, id)
			q := o.TraceQueryStart(c.Request().Context(), nil, pgx.TraceQueryStartData{})
			o.TraceQueryEnd(q, nil, pgx.TraceQueryEndData{})
			return next(c)
		}
	})
	parent, cancel := context.WithDeadline(context.Background(), time.Now().Add(time.Hour))
	defer cancel()
	e.POST("/private-path", func(c echo.Context) error {
		body, err := io.ReadAll(c.Request().Body)
		if err != nil || string(body) != "REQUEST_SECRET" {
			t.Fatal("request body changed")
		}
		if a, ok := c.Request().Context().Deadline(); !ok {
			t.Fatal("missing deadline")
		} else if b, _ := parent.Deadline(); a != b {
			t.Fatal("changed deadline")
		}
		cancel()
		if c.Request().Context().Err() != context.Canceled {
			t.Fatal("lost cancellation")
		}
		c.Response().Header().Set("X-Existing", "kept")
		return c.String(http.StatusAccepted, "BODY_SECRET")
	})
	e.GET("/error", func(c echo.Context) error { return echo.NewHTTPError(http.StatusConflict, "same conflict") })
	for i, path := range []string{"/private-path?token=URL_SECRET", "/error", "/missing"} {
		method := "GET"
		if i == 0 {
			method = "POST"
		}
		r := httptest.NewRequest(method, path, strings.NewReader("REQUEST_SECRET")).WithContext(parent)
		r.Header.Set("X-Query-Observation-ID", "HEADER_SECRET")
		r.Header.Set("Authorization", "CREDENTIAL_SECRET")
		w := httptest.NewRecorder()
		e.ServeHTTP(w, r)
		if w.Header().Get("X-Query-Observation-ID") != ids[i] {
			t.Fatal("header lost")
		}
		if i == 0 && (w.Code != 202 || w.Body.String() != "BODY_SECRET" || w.Header().Get("X-Existing") != "kept") {
			t.Fatal("response changed")
		}
		if i == 1 && (w.Code != 409 || w.Body.String() != "{\"message\":\"same conflict\"}\n") {
			t.Fatalf("error changed: %d %s", w.Code, w.Body)
		}
		if i == 2 && w.Code != 404 {
			t.Fatal("missing route changed")
		}
	}
	if err := o.Close(); err != nil {
		t.Fatal(err)
	}
	var summaries []string
	decoder := json.NewDecoder(&sink)
	for decoder.More() {
		var r struct {
			Type        string `json:"type"`
			RequestID   string `json:"request_id"`
			QueryStarts int    `json:"query_starts"`
			Complete    bool   `json:"complete"`
		}
		if err := decoder.Decode(&r); err != nil {
			t.Fatal(err)
		}
		if r.Type == "request" {
			summaries = append(summaries, r.RequestID)
			if r.QueryStarts != 1 || !r.Complete {
				t.Fatal("query escaped request")
			}
		}
	}
	if strings.Join(ids, ",") != strings.Join(summaries, ",") {
		t.Fatalf("headers=%v summaries=%v", ids, summaries)
	}
	// Decoder consumed sink; privacy check is separately exercised below.
}

func TestQueryObservationDoesNotLeakHTTPDataOrChangeReturnedError(t *testing.T) {
	var b bytes.Buffer
	o, _ := queryobservation.New(&b, "11111111-2222-4333-8444-555555555555")
	e := echo.New()
	r := httptest.NewRequest("POST", "/PATH_SECRET?token=URL_SECRET", strings.NewReader("BODY_SECRET"))
	r.Header.Set("X-Query-Observation-ID", "HEADER_SECRET")
	r.Header.Set("Authorization", "AUTH_SECRET")
	c := e.NewContext(r, httptest.NewRecorder())
	want := errors.New("ERROR_SECRET")
	got := QueryObservation(o)(func(c echo.Context) error { return want })(c)
	if got != want {
		t.Fatal("returned error replaced")
	}
	if err := o.Close(); err != nil {
		t.Fatal(err)
	}
	for _, s := range []string{"PATH_SECRET", "URL_SECRET", "BODY_SECRET", "HEADER_SECRET", "AUTH_SECRET", "ERROR_SECRET"} {
		if strings.Contains(b.String(), s) {
			t.Fatalf("leaked %s", s)
		}
	}
}

type observationWriter struct{ *httptest.ResponseRecorder }

func (w *observationWriter) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	return nil, nil, http.ErrNotSupported
}
func (w *observationWriter) Push(string, *http.PushOptions) error { return http.ErrNotSupported }

func TestQueryObservationPreservesWriterAndNilIsIdentity(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		var b bytes.Buffer
		var o *queryobservation.Observer
		if enabled {
			o, _ = queryobservation.New(&b, "11111111-2222-4333-8444-555555555555")
		}
		e := echo.New()
		r := httptest.NewRequest("GET", "/", nil)
		w := &observationWriter{httptest.NewRecorder()}
		c := e.NewContext(r, w)
		want := errors.New("original error")
		err := QueryObservation(o)(func(c echo.Context) error {
			if c.Response().Writer != w {
				t.Fatal("response writer wrapped/replaced")
			}
			if _, ok := c.Response().Writer.(http.Flusher); !ok {
				t.Fatal("Flush lost")
			}
			if _, ok := c.Response().Writer.(http.Hijacker); !ok {
				t.Fatal("Hijack lost")
			}
			if _, ok := c.Response().Writer.(http.Pusher); !ok {
				t.Fatal("Push lost")
			}
			if !enabled && c.Request() != r {
				t.Fatal("disabled middleware replaced request")
			}
			return want
		})(c)
		if err != want {
			t.Fatal("error changed")
		}
		if !enabled && w.Header().Get("X-Query-Observation-ID") != "" {
			t.Fatal("nil middleware set header")
		}
		if enabled {
			if err := o.Close(); err != nil {
				t.Fatal(err)
			}
		}
	}
}

func TestQueryObservationFinishesWithoutSwallowingPanic(t *testing.T) {
	var b bytes.Buffer
	o, _ := queryobservation.New(&b, "11111111-2222-4333-8444-555555555555")
	func() {
		defer func() {
			if recover() != "handler panic" {
				t.Fatal("panic changed")
			}
		}()
		c := echo.New().NewContext(httptest.NewRequest("GET", "/", nil), httptest.NewRecorder())
		_ = QueryObservation(o)(func(echo.Context) error { panic("handler panic") })(c)
	}()
	if err := o.Close(); err != nil {
		t.Fatal(err)
	}
	if strings.Count(b.String(), `"type":"request"`) != 1 {
		t.Fatal("panic lost finish")
	}
}
