package handler

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/labstack/echo/v4"
)

func TestDjangoSlashResolves(t *testing.T) {
	for path, want := range map[string]bool{
		"/folders/": true, "/folders/abc/": true, "/documents/bulk_delete/": true, "/subscription/memberships/x/": true,
		"/folders/a.b/": false, "/folders/a/b/": false, "/chat/test/": true, "/schema/": true, "/schema/redoc/": true,
		"/document/invoice/abc/view/": true, "/document/invoice//view/": false, "/document/medical/certificate/": true,
		"/no-such-route/": false, "/instance/status/": false,
	} {
		if got := djangoSlashResolves(path); got != want {
			t.Errorf("%s: %v, want %v", path, got, want)
		}
	}
}

func TestDjangoEmptySegmentAnswersUnmatched(t *testing.T) {
	e := echo.New()
	e.HTTPErrorHandler = DjangoHTTPErrorHandler(e)
	e.Use(DjangoEmptySegment(DjangoNotFound))
	e.Any("/collaborators/:uid/update", func(c echo.Context) error { return c.String(http.StatusTeapot, c.Param("uid")) })
	e.Any("/audit-logs/:id/detail", func(echo.Context) error { return echo.ErrNotFound })
	e.Any("/*", DjangoNotFound)
	for path, want := range map[string]int{
		"/collaborators/x/update": http.StatusTeapot, "/collaborators//update": http.StatusNotFound,
		"/audit-logs/1a/detail": http.StatusNotFound, "/folders": http.StatusMovedPermanently, "/": http.StatusNotFound,
	} {
		recorder := httptest.NewRecorder()
		e.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, path, nil))
		if recorder.Code != want {
			t.Errorf("%s: %d, want %d", path, recorder.Code, want)
		}
		if want == http.StatusNotFound && recorder.Body.String() != string(djangoNotFoundPage) {
			t.Errorf("%s: body %q", path, recorder.Body.String())
		}
	}
}
