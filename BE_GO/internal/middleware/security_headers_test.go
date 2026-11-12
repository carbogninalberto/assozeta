package middleware

import (
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/labstack/echo/v4"
)

func TestSecurityHeadersPreserveReturnedErrorAndCORSOrder(t *testing.T) {
	for _, origins := range [][]string{{"https://allowed.example.test"}, {"https://[invalid"}} {
		e := echo.New()
		e.Pre(DjangoCORS(false, origins), DjangoSecurityHeaders())
		e.GET("/error", func(c echo.Context) error { return echo.NewHTTPError(409, "fixture conflict") })
		r := httptest.NewRequest("GET", "/error", nil)
		r.Header.Set("Origin", "https://allowed.example.test")
		w := httptest.NewRecorder()
		e.ServeHTTP(w, r)
		if strings.Contains(origins[0], "[invalid") {
			if w.Code != 500 || w.Header().Get("X-Content-Type-Options") != "" || w.Header().Get("Referrer-Policy") != "" || w.Header().Get("Cross-Origin-Opener-Policy") != "" {
				t.Fatal("outer CORS exception retained discarded security headers")
			}
		} else if w.Code != 409 || w.Header().Get("X-Content-Type-Options") != "nosniff" || w.Header().Get("Referrer-Policy") != "same-origin" || w.Header().Get("Cross-Origin-Opener-Policy") != "same-origin" {
			t.Fatal("returned error lost security headers")
		}
	}
}
