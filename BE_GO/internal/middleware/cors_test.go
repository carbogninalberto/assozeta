package middleware

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/labstack/echo/v4"
)

func TestCORSPreflightBypassesSessionAndReturnedErrorsKeepHeaders(t *testing.T) {
	e := echo.New()
	e.Pre(DjangoCORS(false, []string{"https://allowed.example.test"}))
	calls := 0
	e.Any("/protected", func(c echo.Context) error { return echo.NewHTTPError(409, "fixture") },
		DjangoSessionAudit(sessionResolverFunc(func(context.Context, string) (domain.SessionIdentity, error) {
			calls++
			return domain.SessionIdentity{Empty: true}, nil
		})))
	for _, method := range []string{"OPTIONS", "GET"} {
		r := httptest.NewRequest(method, "/protected", nil)
		r.Header.Set("Origin", "https://allowed.example.test")
		r.Header.Set("Cookie", "sessionid=missing")
		r.Header.Set("Access-Control-Request-Method", "")
		w := httptest.NewRecorder()
		e.ServeHTTP(w, r)
		if w.Header().Get("Access-Control-Allow-Origin") != "https://allowed.example.test" {
			t.Fatal("CORS headers missing")
		}
		if method == "OPTIONS" {
			if calls != 0 || w.Code != 200 || w.Body.Len() != 0 || w.Header().Get("Vary") != "origin" || w.Header().Get("Set-Cookie") != "" {
				t.Fatal("preflight entered session or handler")
			}
		} else if calls != 1 || w.Code != 409 || w.Header().Get("Vary") != "Cookie, origin" || w.Header().Get("Set-Cookie") == "" {
			t.Fatal("returned error lost session/CORS response composition")
		}
	}
}

func TestCORSInvalidPolicyPreservesEffectsAfterWriteAndReplacesHeaders(t *testing.T) {
	e := echo.New()
	e.Pre(DjangoCORS(false, []string{"https://[invalid"}))
	finished := false
	e.GET("/write", func(c echo.Context) error {
		c.Response().Header().Set("Set-Cookie", "private=fixture")
		c.Response().Header().Set("Content-Length", "3")
		if err := c.Blob(201, "application/octet-stream", []byte("abc")); err != nil {
			return err
		}
		if err := http.NewResponseController(c.Response().Writer).Flush(); err != nil {
			return err
		}
		finished = true
		return nil
	})
	r := httptest.NewRequest("GET", "/write", nil)
	r.Header.Set("Origin", "https://allowed.example.test")
	w := httptest.NewRecorder()
	e.ServeHTTP(w, r)
	if !finished || w.Code != 500 || !strings.Contains(w.Body.String(), "Server Error (500)") {
		t.Fatal("CORS configuration failure skipped handler effects or failed to replace response")
	}
	for _, name := range []string{"Set-Cookie", "Content-Length", "Vary", "Access-Control-Allow-Origin"} {
		if w.Header().Get(name) != "" {
			t.Fatalf("replaced response retained %s", name)
		}
	}
}
