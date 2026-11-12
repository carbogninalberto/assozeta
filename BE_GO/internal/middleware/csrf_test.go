package middleware

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/labstack/echo/v4"
)

func TestDjangoCSRFAdmission(t *testing.T) {
	secret := strings.Repeat("a", 32)
	for _, tc := range []struct {
		name, method, cookie, token, origin, referer string
		secure                                       bool
		status                                       int
	}{
		{name: "safe", method: "GET", status: 204},
		{name: "head", method: "HEAD", status: 204},
		{name: "missing", method: "POST", status: 403},
		{name: "matching", method: "POST", cookie: secret, token: secret, status: 204},
		{name: "masked", method: "POST", cookie: secret, token: strings.Repeat("b", 64), status: 204},
		{name: "mismatch", method: "POST", cookie: secret, token: strings.Repeat("b", 32), status: 403},
		{name: "invalid alphabet", method: "POST", cookie: secret, token: strings.Repeat("!", 32), status: 403},
		{name: "foreign origin", method: "POST", cookie: secret, token: secret, origin: "https://evil.test", status: 403},
		{name: "matching origin", method: "POST", cookie: secret, token: secret, origin: "http://api.test", status: 204},
		{name: "trusted origin", method: "POST", cookie: secret, token: secret, origin: "https://ui.test", status: 204},
		{name: "https missing referer", method: "POST", cookie: secret, token: secret, secure: true, status: 403},
		{name: "https matching referer", method: "POST", cookie: secret, token: secret, secure: true, referer: "https://api.test/page", status: 204},
		{name: "https bad referer", method: "POST", cookie: secret, token: secret, secure: true, referer: "http://api.test/page", status: 403},
	} {
		t.Run(tc.name, func(t *testing.T) {
			e := echo.New()
			e.Any("/healthz", func(c echo.Context) error { return c.NoContent(204) }, DjangoCSRF([]string{"https://ui.test"}))
			req := httptest.NewRequest(tc.method, "http://api.test/healthz", nil)
			if tc.cookie != "" {
				req.AddCookie(&http.Cookie{Name: "csrftoken", Value: tc.cookie})
			}
			req.Header.Set("X-CSRFToken", tc.token)
			if tc.origin != "" {
				req.Header.Set("Origin", tc.origin)
			}
			req.Header.Set("Referer", tc.referer)
			if tc.secure {
				req.Header.Set("X-Forwarded-Proto", "https")
			}
			res := httptest.NewRecorder()
			e.ServeHTTP(res, req)
			if res.Code != tc.status {
				t.Fatalf("status=%d, want %d", res.Code, tc.status)
			}
		})
	}
}
