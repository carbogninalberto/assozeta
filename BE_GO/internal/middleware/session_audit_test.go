package middleware

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/labstack/echo/v4"
)

type sessionResolverFunc func(context.Context, string) (domain.SessionIdentity, error)

func (f sessionResolverFunc) Resolve(ctx context.Context, key string) (domain.SessionIdentity, error) {
	return f(ctx, key)
}

func TestSessionAuditRequiresTrustedMiddleware(t *testing.T) {
	r := httptest.NewRequest("POST", "/oauth2/login", nil)
	r.Header.Set("User-Id", "forged")
	r.Header.Set("Authorization", "Bearer forged")
	if _, err := SessionAuditContext(r); !errors.Is(err, domain.ErrInvalidScopedRequest) {
		t.Fatal("forged headers supplied trusted audit context")
	}
}

func TestSessionFailureNeverInvokesViewAndStillVaries(t *testing.T) {
	for _, failure := range []error{errors.New("fixture cache deletion failed"), context.Canceled} {
		e := echo.New()
		called := false
		e.Any("/*", func(c echo.Context) error { called = true; return c.NoContent(204) }, DjangoSessionAudit(sessionResolverFunc(func(context.Context, string) (domain.SessionIdentity, error) {
			return domain.SessionIdentity{}, failure
		})))
		r := httptest.NewRequest("POST", "/oauth2/login", nil)
		r.Header.Set("Cookie", "sessionid=fixture-session")
		w := httptest.NewRecorder()
		e.ServeHTTP(w, r)
		if w.Header().Get("Content-Type") != "text/html; charset=utf-8" || strings.Contains(w.Body.String(), failure.Error()) {
			t.Fatal("session failure response exposes internals or changes media type")
		}
		if called || w.Code != 500 || w.Header().Get("Vary") != "Cookie" || w.Header().Get("Set-Cookie") != "" {
			t.Fatal("session failure crossed view/cookie boundary")
		}
	}
}

func TestSessionAuditIsAvailableBeforeReturnedHandlerError(t *testing.T) {
	e := echo.New()
	e.Any("/*", func(c echo.Context) error {
		audit, err := SessionAuditContext(c.Request())
		if err != nil || audit.ActorID == nil || *audit.ActorID != "fixture-actor" {
			t.Fatal("pre-view actor absent")
		}
		c.Response().Header().Set("Vary", "Origin")
		return echo.NewHTTPError(http.StatusConflict, "fixture conflict")
	}, DjangoSessionAudit(sessionResolverFunc(func(context.Context, string) (domain.SessionIdentity, error) {
		return domain.SessionIdentity{Actor: &domain.SessionActor{ID: "fixture-actor", Email: "fixture@example.test"}}, nil
	})))
	w := httptest.NewRecorder()
	e.ServeHTTP(w, httptest.NewRequest("POST", "/oauth2/login", nil))
	if w.Code != 409 || w.Header().Get("Vary") != "Origin, Cookie" {
		t.Fatal("returned handler error lost session response headers")
	}
}

func TestSessionAuditHeadersPrecedeEarlyFlush(t *testing.T) {
	e := echo.New()
	e.Any("/*", func(c echo.Context) error {
		c.Response().Header().Set("Vary", "Origin")
		c.Response().Flush()
		_, err := c.Response().Write([]byte("frame"))
		return err
	}, DjangoSessionAudit(sessionResolverFunc(func(context.Context, string) (domain.SessionIdentity, error) {
		return domain.SessionIdentity{Empty: true}, nil
	})))
	r := httptest.NewRequest("GET", "/oauth2/login", nil)
	r.Header.Set("Cookie", "sessionid=short")
	w := httptest.NewRecorder()
	e.ServeHTTP(w, r)
	response := w.Result()
	defer response.Body.Close()
	if response.Header.Get("Vary") != "Origin, Cookie" || response.Header.Get("Set-Cookie") == "" || w.Body.String() != "frame" {
		t.Fatal("flush committed response before session headers")
	}
}

func TestSessionAuditWriterPreservesImplicitContentType(t *testing.T) {
	w := httptest.NewRecorder()
	wrapped := &sessionHeaderWriter{ResponseWriter: w, before: func() { w.Header().Set("Vary", "Cookie") }}
	if _, err := wrapped.Write([]byte("fixture text")); err != nil {
		t.Fatal(err)
	}
	if w.Result().Header.Get("Content-Type") != "text/plain; charset=utf-8" || w.Result().Header.Get("Vary") != "Cookie" {
		t.Fatal("session writer changed implicit HTTP response behavior")
	}
}

func TestSessionAuditVaryDelimiterNormalization(t *testing.T) {
	for value, want := range map[string]string{"Origin,cookie": "Origin, cookie", "Origin,  Accept-Language": "Origin, Accept-Language, Cookie", "": ", Cookie"} {
		header := http.Header{"Vary": []string{value}}
		varySessionCookie(header)
		if header.Get("Vary") != want {
			t.Errorf("Vary %q became %q, want %q", value, header.Get("Vary"), want)
		}
	}
}
