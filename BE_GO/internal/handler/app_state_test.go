package handler

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/labstack/echo/v4"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

func TestAppStateAnswersDjangoServerErrorPage(t *testing.T) {
	e := echo.New()
	e.HTTPErrorHandler = DjangoHTTPErrorHandler(e)
	e.GET("/x", func(echo.Context) error { return refusal.AppState("cached value of another shape") })
	recorder := httptest.NewRecorder()
	e.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/x", nil))
	if recorder.Code != 500 || recorder.Body.String() != string(djangoServerErrorPage) {
		t.Fatalf("got %d %q", recorder.Code, recorder.Body.String())
	}
}
