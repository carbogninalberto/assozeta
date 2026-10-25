package handler

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/labstack/echo/v4"
)

func TestManifestLocaleAtProductionResponseBoundary(t *testing.T) {
	for _, tc := range []struct{ language, cookie, want string }{
		{"de", "", "de"}, {"fr", "django_language=ja", "ja"},
		{"zh-TW", "", "zh-hant"}, {"", "", "it"},
	} {
		for _, shortCircuit := range []bool{false, true} {
			t.Run(tc.want+"/short="+map[bool]string{false: "false", true: "true"}[shortCircuit], func(t *testing.T) {
				e := echo.New()
				e.Pre(InstanceBootstrapLocale())
				if shortCircuit {
					e.Pre(func(next echo.HandlerFunc) echo.HandlerFunc {
						return func(c echo.Context) error {
							return c.JSON(http.StatusServiceUnavailable, map[string]string{"detail": "fixture maintenance"})
						}
					})
				}
				h := NewInstanceManifestHandler(manifestReaderFunc(func(context.Context) (*dto.InstanceManifest, error) {
					t.Fatal("OPTIONS must not read the manifest repository")
					return nil, nil
				}))
				e.Any("/instance/manifest.json", h.Read)
				r := httptest.NewRequest(http.MethodOptions, "/instance/manifest.json", nil)
				r.Header.Set("Accept-Language", tc.language)
				r.Header.Set("Cookie", tc.cookie)
				w := httptest.NewRecorder()
				e.ServeHTTP(w, r)
				wantStatus := http.StatusOK
				if shortCircuit {
					wantStatus = http.StatusServiceUnavailable
				}
				if w.Code != wantStatus || w.Header().Get("Content-Language") != tc.want {
					t.Fatalf("status=%d language=%q want status=%d language=%q", w.Code, w.Header().Get("Content-Language"), wantStatus, tc.want)
				}
				if !strings.Contains(strings.Join(w.Header().Values("Vary"), ","), "Accept-Language") {
					t.Fatal("locale-dependent response omitted Accept-Language variance")
				}
			})
		}
	}
}
