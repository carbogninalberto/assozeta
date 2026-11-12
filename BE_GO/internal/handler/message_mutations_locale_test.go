package handler

import (
	"context"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/middleware"
	"github.com/labstack/echo/v4"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
)

type messageMutationDeniedGate struct{ err error }

func (g messageMutationDeniedGate) AcquireHTTP(context.Context, string, string) (io.Closer, error) {
	return nil, g.err
}
func TestMessageMutationLocaleSurvivesRestoreAdmission(t *testing.T) {
	for _, path := range []string{"/communications/messages/add", "/communications/messages/a1000000-0000-4000-8000-000000720004/delete"} {
		for _, failure := range []error{domain.ErrRestorePending, domain.ErrRestoreBusy} {
			t.Run(path+failure.Error(), func(t *testing.T) {
				e := echo.New()
				e.Pre(InstanceBootstrapLocale(), middleware.DjangoRestoreAdmission(messageMutationDeniedGate{failure}))
				e.Any(path, func(c echo.Context) error { t.Error("restore denial reached handler"); return c.NoContent(200) })
				req := httptest.NewRequest(http.MethodPost, path, nil)
				req.Header.Set("Accept-Language", "en")
				rec := httptest.NewRecorder()
				e.ServeHTTP(rec, req)
				if rec.Code != 503 || rec.Header().Get("Retry-After") != "10" || rec.Header().Get("Allow") != "" {
					t.Fatalf("admission changed: %d %v", rec.Code, rec.Header())
				}
				if rec.Header().Get("Content-Language") != "en" {
					t.Fatalf("missing reference locale: %v", rec.Header())
				}
			})
		}
	}
}
