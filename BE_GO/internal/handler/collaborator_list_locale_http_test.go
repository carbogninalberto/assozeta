package handler

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

func localizedCollaboratorHandler(role int) *CollaboratorListHandler {
	accounts := paymentInfoAccounts{domain.Account{ID: "a1000000-0000-4000-8000-000000220008", Active: true, Role: role}}
	return NewCollaboratorListHandler(
		&collaboratorHandlerReader{},
		service.NewActorAuthentication(accounts, &paymentInfoTokens{}),
		service.NewIdentityResolver(accounts, nil, nil, service.NewPermissions(), nil),
	)
}

func TestCollaboratorLocalizedTransportErrors(t *testing.T) {
	for _, test := range []struct {
		name, method, target, accept, language, authorization string
		status                                                int
		detail                                                string
	}{
		{"english credentials", "GET", "/collaborators/list", "", "en", "", 401, "Authentication credentials were not provided."},
		{"german permission", "GET", "/collaborators/list", "", "de", "Bearer valid", 403, "Sie sind nicht berechtigt, diese Aktion durchzuführen."},
		{"spanish accept", "GET", "/collaborators/list", "text/html", "es-AR", "", 406, "No se ha podido satisfacer la solicitud de cabecera de Accept."},
		{"french format", "GET", "/collaborators/list?format=xml", "", "fr", "", 404, "Non trouvé."},
		{"japanese method", "POST", "/collaborators/list", "", "ja", "Bearer valid", 405, `メソッド "POST" は許されていません。`},
		{"traditional chinese permission", "GET", "/collaborators/list", "", "zh-Hant", "Bearer valid", 403, "你沒有執行此操作的權限。"},
		{"vietnamese credential fallback", "GET", "/collaborators/list", "", "vi", "", 401, "Non sono state immesse le credenziali di autenticazione."},
		{"lithuanian permission fallback", "GET", "/collaborators/list", "", "lt", "Bearer valid", 403, "Non hai l'autorizzazione per eseguire questa azione."},
	} {
		t.Run(test.name, func(t *testing.T) {
			role := domain.RoleAssociation
			if strings.Contains(test.name, "permission") {
				role = domain.RoleAthlete
			}
			h := localizedCollaboratorHandler(role)
			e := echo.New()
			e.Any("/collaborators/list", h.Read)
			r := httptest.NewRequest(test.method, test.target, nil)
			r.Header.Set("Accept-Language", test.language)
			r.Header.Set("Accept", test.accept)
			r.Header.Set("Authorization", test.authorization)
			w := httptest.NewRecorder()
			e.ServeHTTP(w, r)
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

func TestCollaboratorLanguageCookieDuplicateLastValue(t *testing.T) {
	h := localizedCollaboratorHandler(domain.RoleAthlete)
	e := echo.New()
	e.Any("/collaborators/list", h.Read)
	r := httptest.NewRequest(http.MethodGet, "/collaborators/list", nil)
	r.Header.Set("Accept-Language", "de")
	r.Header.Set("Cookie", "django_language=ja; django_language=es-AR")
	r.Header.Set("Authorization", "Bearer valid")
	w := httptest.NewRecorder()
	e.ServeHTTP(w, r)
	if w.Code != 403 || w.Header().Get("Content-Language") != "es-ar" ||
		!strings.Contains(w.Body.String(), "Usted no tiene permiso") {
		t.Fatalf("status=%d language=%q body=%s", w.Code, w.Header().Get("Content-Language"), w.Body.String())
	}
}

var _ readRouteLocale = djangoReadRouteLocale{}
var _ CollaboratorListReader = (*collaboratorHandlerReader)(nil)
