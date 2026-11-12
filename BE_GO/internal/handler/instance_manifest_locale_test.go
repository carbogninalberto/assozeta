package handler

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/labstack/echo/v4"
)

func TestManifestLocalizedTransportErrors(t *testing.T) {
	for _, test := range []struct {
		name, method, target, accept, language string
		status                                 int
		detail                                 string
	}{
		{"german format", "GET", "/instance/manifest.json?format=xml", "", "de", 404, "Nicht gefunden."},
		{"spanish method", "POST", "/instance/manifest.json", "", "ES-ar", 405, `Método "POST" no permitido.`},
		{"chinese accept fallback", "GET", "/instance/manifest.json", "text/html", "zh-TW", 406, `Impossibile soddisfare l'header "Accept" presente nella richiesta.`},
		{"vietnamese method", "POST", "/instance/manifest.json", "", "vi", 405, "Phương thức “POST” không được chấp nhận."},
		{"afrikaans fallback", "POST", "/instance/manifest.json", "", "af", 405, `Metodo "POST" non consentito`},
	} {
		t.Run(test.name, func(t *testing.T) {
			h := NewInstanceManifestHandler(manifestReaderFunc(func(context.Context) (*dto.InstanceManifest, error) {
				t.Fatal("localized transport error read the database")
				return nil, nil
			}))
			r := httptest.NewRequest(test.method, test.target, nil)
			r.Header.Set("Accept-Language", test.language)
			r.Header.Set("Accept", test.accept)
			w := httptest.NewRecorder()
			if err := h.Read(echo.New().NewContext(r, w)); err != nil {
				t.Fatal(err)
			}
			var body map[string]string
			if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
				t.Fatal(err)
			}
			if w.Code != test.status || body["detail"] != test.detail ||
				w.Header().Get("Content-Language") != handlerReadRouteLocale.Language(r) {
				t.Fatalf("status=%d language=%q body=%s", w.Code, w.Header().Get("Content-Language"), w.Body.String())
			}
		})
	}
}

func TestManifestOptionsKeepsAuthoredLiterals(t *testing.T) {
	h := NewInstanceManifestHandler(manifestReaderFunc(func(context.Context) (*dto.InstanceManifest, error) {
		t.Fatal("OPTIONS read the database")
		return nil, nil
	}))
	r := httptest.NewRequest(http.MethodOptions, "/instance/manifest.json", nil)
	r.Header.Set("Accept-Language", "de")
	w := httptest.NewRecorder()
	if err := h.Read(echo.New().NewContext(r, w)); err != nil {
		t.Fatal(err)
	}
	var body map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body["name"] != "Instance Manifest" || body["description"] != "GET /instance/manifest.json\nDynamic PWA manifest." {
		t.Fatalf("authored OPTIONS changed: %#v", body)
	}
}
