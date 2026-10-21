package middleware

import (
	"context"
	"errors"
	"io"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type loginRateStub struct {
	calls []string
	err   error
}

func (s *loginRateStub) Check(_ context.Context, group, value string) error {
	s.calls = append(s.calls, group+":"+value)
	return s.err
}
func (s *loginRateStub) CheckAccount(_ context.Context, body any) error {
	s.calls = append(s.calls, "account")
	return s.err
}

type forbiddenRateBody struct{ read bool }

func (r *forbiddenRateBody) Read([]byte) (int, error) {
	r.read = true
	return 0, errors.New("body must not be read")
}
func (r *forbiddenRateBody) Close() error { return nil }

func TestLoginRateIPDenialPrecedesBodyAndNext(t *testing.T) {
	gate := &loginRateStub{err: service.ErrLoginRateLimited}
	body := &forbiddenRateBody{}
	r := httptest.NewRequest("POST", "/oauth2/login", nil)
	r.Body = body
	r.Header.Set("X-Forwarded-For", " 203.0.113.8 , 192.0.2.1")
	w := httptest.NewRecorder()
	e := echo.New()
	c := e.NewContext(r, w)
	called := false
	err := LoginRateLimit(true, gate)(func(echo.Context) error { called = true; return nil })(c)
	if err != nil || called || body.read || w.Code != 429 || w.Body.String() != "Too many requests, blocked." || len(gate.calls) != 1 || gate.calls[0] != "login-ip:203.0.113.8" {
		t.Fatal("IP gate ordering/response differs")
	}
	if w.Header().Get("Content-Type") != "text/html; charset=utf-8" {
		t.Fatal("429 media type differs")
	}
}
func TestLoginRatePreservesBodyAndBypassesDisabledOrOtherMethods(t *testing.T) {
	for _, tc := range []struct {
		enabled bool
		method  string
		want    int
	}{{true, "POST", 2}, {false, "POST", 0}, {true, "GET", 0}, {true, "OPTIONS", 0}} {
		gate := &loginRateStub{}
		raw := `{"username":"Alice"}`
		r := httptest.NewRequest(tc.method, "/oauth2/login", strings.NewReader(raw))
		w := httptest.NewRecorder()
		e := echo.New()
		called := false
		err := LoginRateLimit(tc.enabled, gate)(func(c echo.Context) error {
			called = true
			body, err := io.ReadAll(c.Request().Body)
			if err != nil || string(body) != raw {
				t.Fatal("body changed")
			}
			return c.NoContent(204)
		})(e.NewContext(r, w))
		if err != nil || !called || len(gate.calls) != tc.want {
			t.Fatal("admission bypass/order differs")
		}
	}
}
