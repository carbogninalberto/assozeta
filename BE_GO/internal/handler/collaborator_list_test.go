package handler

import (
	"context"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type collaboratorHandlerReader struct {
	calls      int
	identifier *string
}

func (r *collaboratorHandlerReader) Read(_ context.Context, _ domain.ResolvedIdentity, id *string) (any, error) {
	r.calls++
	r.identifier = id
	return []any{}, nil
}
func TestCollaboratorTransportPrecedence(t *testing.T) {
	for _, tc := range []struct {
		method, query, accept, authorization string
		role, status, tokens, reads          int
	}{
		{"GET", "?format=xml", "text/html", "Bearer invalid", 1, 404, 0, 0},
		{"GET", "", "text/html", "Bearer invalid", 1, 406, 0, 0},
		{"POST", "", "", "Bearer invalid", 1, 401, 1, 0},
		{"OPTIONS", "", "", "", 1, 401, 0, 0},
		{"POST", "", "", "Bearer valid", 2, 403, 1, 0},
		{"OPTIONS", "", "", "Bearer valid", 1, 200, 1, 0},
		{"POST", "", "", "Bearer valid", 1, 405, 1, 0},
		{"GET", "?format=&user_id=old&user_id=new", "", "Bearer valid", 1, 200, 1, 1},
	} {
		tokens := &paymentInfoTokens{}
		reader := &collaboratorHandlerReader{}
		accounts := paymentInfoAccounts{domain.Account{ID: "a1000000-0000-4000-8000-000000220008", Active: true, Role: tc.role}}
		h := NewCollaboratorListHandler(reader, service.NewActorAuthentication(accounts, tokens), service.NewIdentityResolver(accounts, nil, nil, service.NewPermissions(), nil))
		e := echo.New()
		e.Any("/collaborators/list", h.Read)
		request := httptest.NewRequest(tc.method, "/collaborators/list"+tc.query, strings.NewReader("invalid body ignored"))
		request.Header.Set("Accept-Language", "en")
		request.Header.Set("Accept", tc.accept)
		request.Header.Set("Authorization", tc.authorization)
		w := httptest.NewRecorder()
		e.ServeHTTP(w, request)
		if w.Code != tc.status || tokens.calls != tc.tokens || reader.calls != tc.reads {
			t.Fatalf("%+v -> %d %s calls %d/%d", tc, w.Code, w.Body.String(), tokens.calls, reader.calls)
		}
		if reader.calls > 0 && (reader.identifier == nil || *reader.identifier != "new") {
			t.Fatal("query last-value semantics lost")
		}
	}
}
