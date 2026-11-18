//go:build handoffprobe

package handler

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// TestAPISchemaHandoffProbe checks the test build's refusal: exactly the probe
// language, a not-ported sentinel (so the hand-off replays it), and the stable
// server error page when nothing replays it.
func TestAPISchemaHandoffProbe(t *testing.T) {
	for lang, want := range map[string]bool{"handoff-probe": true, "xx": false, "": false, "handoff-probe/": false} {
		err := apiSchemaHandoffProbe(lang)
		if got := err != nil; got != want {
			t.Errorf("apiSchemaHandoffProbe(%q) refuses = %v, want %v", lang, got, want)
			continue
		}
		if err != nil && (!errors.Is(err, errAPISchemaHandoffProbe) || !errors.Is(err, refusal.ErrNotPorted)) {
			t.Errorf("apiSchemaHandoffProbe(%q) = %v, want a not-ported sentinel", lang, err)
		}
	}

	handler := NewAPISchemaHandler(service.NewActorAuthentication(nil, nil),
		service.NewIdentityResolver(nil, nil, nil, nil, nil), false)
	e := echo.New()
	e.GET(apiSchemaPath, handler.Schema)
	request := httptest.NewRequest(http.MethodGet, apiSchemaPath+"?lang=handoff-probe", nil)
	request.Header.Set("Accept", "application/json")
	recorder := httptest.NewRecorder()
	e.ServeHTTP(recorder, request)
	if recorder.Code != http.StatusInternalServerError {
		t.Fatalf("?lang=handoff-probe: status %d, want 500", recorder.Code)
	}
	if body := recorder.Body.String(); !strings.Contains(body, "Server Error (500)") {
		t.Errorf("?lang=handoff-probe: body is not the server error page: %q", body)
	}
	if allow := recorder.Header().Get("Allow"); allow != "" {
		t.Errorf("?lang=handoff-probe: Allow = %q, want it dropped", allow)
	}
}
