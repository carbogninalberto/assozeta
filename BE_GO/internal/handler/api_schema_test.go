//go:build !handoffprobe

package handler

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/apischema"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// serveSchema answers GET /schema/ for one lang value with the handler's echo
// route, without the handoff middleware (no state, so nothing is replayed).
func serveSchema(t *testing.T, lang string) *httptest.ResponseRecorder {
	t.Helper()
	handler := NewAPISchemaHandler(service.NewActorAuthentication(nil, nil),
		service.NewIdentityResolver(nil, nil, nil, nil, nil), false)
	e := echo.New()
	e.GET(apiSchemaPath, handler.Schema)
	request := httptest.NewRequest(http.MethodGet, apiSchemaPath+"?lang="+lang, nil)
	request.Header.Set("Accept", "application/json")
	recorder := httptest.NewRecorder()
	e.ServeHTTP(recorder, request)
	return recorder
}

// mustDocument is the JSON document the generator stores for a language.
func mustDocument(t *testing.T, language string) string {
	t.Helper()
	key, ok := apischema.CatalogKey(language)
	if !ok {
		t.Fatalf("CatalogKey(%q) is refused", language)
	}
	document, ok := apischema.Document(apischema.FormatJSON, key, "")
	if !ok {
		t.Fatalf("no document for %q (key %q)", language, key)
	}
	return document
}

// A `lang` with a path separator walks the catalog directories; the handler
// answers it from the tables, with the document Django renders (measured with
// the reference Python: these select Italian or no catalog at all).
func TestSchemaAnswersLanguagesWithPathSeparators(t *testing.T) {
	for lang, reference := range map[string]string{
		"it/../it":     "it",
		"it/":          "it",
		"en/../it":     "it",
		"xx/../it":     "xx",
		"xx%2F..%2Fit": "xx",
		"/tmp":         "xx",
		"xx/yy":        "xx",
		// "en/xx" selects nothing: the flag decides the document Django renders
		// ("en" without a fallback, the default language with one). "en/../xx"
		// splits at the first dot (the ".."), so its "en/" expansion selects en.
		"en/xx":    "en",
		"en/../xx": "en",
	} {
		recorder := serveSchema(t, lang)
		if recorder.Code != http.StatusOK {
			t.Errorf("?lang=%s: status %d, want 200", lang, recorder.Code)
			continue
		}
		if want := mustDocument(t, reference); recorder.Body.String() != want {
			t.Errorf("?lang=%s: document differs from the %q document", lang, reference)
		}
		if got, want := recorder.Header().Get("Content-Type"), "application/json"; got != want {
			t.Errorf("?lang=%s: Content-Type = %q, want %q", lang, got, want)
		}
		if got, want := recorder.Header().Get("Allow"), "GET, HEAD, OPTIONS"; got != want {
			t.Errorf("?lang=%s: Allow = %q, want %q", lang, got, want)
		}
		if got, want := recorder.Header().Get("Content-Disposition"), `inline; filename="`+apischema.Title()+`.json"`; got != want {
			t.Errorf("?lang=%s: Content-Disposition = %q, want %q", lang, got, want)
		}
	}
}

// The hand-off probe sentinel exists only in the handoffprobe build; the default
// build answers its language like any other unknown language.
func TestSchemaAnswersTheHandoffProbeLanguage(t *testing.T) {
	if err := apiSchemaHandoffProbe("handoff-probe"); err != nil {
		t.Fatalf("apiSchemaHandoffProbe refuses in the default build: %v", err)
	}
	recorder := serveSchema(t, "handoff-probe")
	if recorder.Code != http.StatusOK {
		t.Fatalf("?lang=handoff-probe: status %d, want 200", recorder.Code)
	}
	if want := mustDocument(t, "xx"); recorder.Body.String() != want {
		t.Errorf("?lang=handoff-probe: document differs from the unknown-language document")
	}
}
