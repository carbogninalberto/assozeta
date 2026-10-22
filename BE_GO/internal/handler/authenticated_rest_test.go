package handler

import (
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/labstack/echo/v4"
)

func TestAuthenticatedRESTBodyRetainsScopedBodyAndError(t *testing.T) {
	for _, body := range []string{`{"enable":false,"secret":"","otp":""}`, `{`} {
		request := httptest.NewRequest("POST", "/two-fa/update", strings.NewReader(body))
		request.Header.Set("Content-Type", "application/json")
		c := echo.New().NewContext(request, httptest.NewRecorder())
		first, firstErr := readAuthenticatedRESTBody(c)
		second, secondErr := readAuthenticatedRESTBody(c)
		if firstErr != secondErr {
			t.Fatal("scoped parser error was lost")
		}
		if firstErr == nil {
			a := first.(domain.RequestObject)
			b := second.(domain.RequestObject)
			if len(a) != 3 || len(b) != 3 {
				t.Fatal("scoped request body was consumed twice")
			}
		}
	}
}
