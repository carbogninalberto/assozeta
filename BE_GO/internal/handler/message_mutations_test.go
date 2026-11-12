package handler

import (
	"context"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
	"net/http"
)

type messageMutationsHTTPFake struct {
	admitted, added, deleted int
	input                    dto.MessageMutationWrite
	id                       string
	err                      error
}

func (f *messageMutationsHTTPFake) Admit(context.Context, domain.ResolvedIdentity) error {
	f.admitted++
	return f.err
}
func (f *messageMutationsHTTPFake) Add(_ context.Context, _ domain.ResolvedIdentity, in dto.MessageMutationWrite, _ domain.LoginAuditContext) error {
	f.added++
	f.input = in
	return f.err
}
func (f *messageMutationsHTTPFake) Delete(_ context.Context, _ domain.ResolvedIdentity, id string, _ domain.LoginAuditContext) error {
	f.deleted++
	f.id = id
	return f.err
}
func TestMessageMutationsHTTPContractAndAdmission(t *testing.T) {
	for _, tc := range []struct {
		name, method, path, body, auth, accept string
		status                                 int
		contains                               string
		add, del                               int
		failure                                error
	}{
		{name: "create", method: "POST", path: "/communications/messages/add", body: `{"type":"EMAIL","message":" <b>x</b> "}`, auth: "Bearer valid", status: 200, contains: `"msg":"message added."`, add: 1},
		{name: "invalid serializer", method: "POST", path: "/communications/messages/add", body: `{"type":"EMAIL","message":""}`, auth: "Bearer valid", status: 400, contains: `"msg":"message not valid."`},
		{name: "malformed", method: "POST", path: "/communications/messages/add", body: `{`, auth: "Bearer valid", status: 400, contains: `"detail"`},
		{name: "auth before body", method: "POST", path: "/communications/messages/add", body: `{`, status: 401, contains: `Authentication credentials were not provided.`},
		{name: "format before accept", method: "POST", path: "/communications/messages/add?format=xml", body: `{`, accept: "text/html", status: 404, contains: `Not found.`},
		{name: "accept before auth", method: "POST", path: "/communications/messages/add", accept: "text/html", status: 406, contains: `Could not satisfy`},
		{name: "options", method: "OPTIONS", path: "/communications/messages/add", auth: "Bearer valid", status: 200, contains: `Communication Messages Add`},
		{name: "wrong method", method: "GET", path: "/communications/messages/add", auth: "Bearer valid", status: 405, contains: `Method`},
		{name: "delete", method: "DELETE", path: "/communications/messages/a1000000-0000-4000-8000-000000702001/delete", body: `{`, auth: "Bearer valid", status: 200, contains: `"msg":"message deleted."`, del: 1},
		{name: "missing", method: "DELETE", path: "/communications/messages/a1000000-0000-4000-8000-000000702001/delete", auth: "Bearer valid", status: 404, contains: `"msg":"message not found."`, del: 1, failure: domain.ErrMessageMutationNotFound},
	} {
		t.Run(tc.name, func(t *testing.T) {
			owner := "a1000000-0000-4000-8000-000000700001"
			accounts := messageReadsAccountsTest{owner: {ID: owner, Role: domain.RoleAssociation, Active: true}}
			tokens := &messageReadsTokensTest{sub: owner}
			fake := &messageMutationsHTTPFake{}
			h := NewMessageMutationsHandler(fake, service.NewActorAuthentication(accounts, tokens), service.NewIdentityResolver(accounts, nil, nil, service.NewPermissions(), nil), func(*http.Request) (domain.LoginAuditContext, error) {
				fake.err = tc.failure
				return domain.LoginAuditContext{}, nil
			}, paymentcategorylifecycle.New())
			e := httptest.NewRecorder()
			r := httptest.NewRequest(tc.method, tc.path, strings.NewReader(tc.body))
			r.Header.Set("Authorization", tc.auth)
			r.Header.Set("Accept-Language", "en")
			r.Header.Set("Accept", tc.accept)
			r.Header.Set("Content-Type", "application/json")
			router := messageMutationsTestRouter(h)
			router.ServeHTTP(e, r)
			if e.Code != tc.status || !strings.Contains(e.Body.String(), tc.contains) || fake.added != tc.add || fake.deleted != tc.del {
				t.Fatalf("response %d %s calls add=%d delete=%d", e.Code, e.Body.String(), fake.added, fake.deleted)
			}
			if tc.name == "create" && fake.input.Message != " <b>x</b> " {
				t.Fatalf("whitespace changed: %#v", fake.input)
			}
		})
	}
}

func messageMutationsTestRouter(h *MessageMutationsHandler) *echo.Echo {
	e := echo.New()
	e.Any("/communications/messages/add", h.Add)
	e.Any("/communications/messages/:message_id/delete", h.Delete)
	return e
}

// Expectations reproduced with production Python 3.13.16 DRF parsers.
func TestMessageMutationsHTTPParserBoundaries(t *testing.T) {
	digits := strings.Repeat("1", 4301)
	for _, tc := range []struct {
		name, media, body, message, contains string
		status                               int
	}{
		{"form invalid byte", "application/x-www-form-urlencoded", "type=EMAIL&message=%FF", "�", "", 200},
		{"form latin1", "application/x-www-form-urlencoded; charset=iso-8859-1", "type=EMAIL&message=%E9", "é", "", 200},
		{"form raw latin1", "application/x-www-form-urlencoded; charset=latin1", "type=EMAIL&message=\xe9", "é", "", 200},
		{"form invalid sequence", "application/x-www-form-urlencoded", "type=EMAIL&message=%ED%A0%80", "���", "", 200},
		{"form adjacent invalid", "application/x-www-form-urlencoded", "type=EMAIL&message=%FF%FF", "��", "", 200},
		{"form truncated sequence", "application/x-www-form-urlencoded", "type=EMAIL&message=%E2%82", "�", "", 200},
		{"form malformed escape", "application/x-www-form-urlencoded", "type=EMAIL&message=%ZZ%FF", "%ZZ�", "", 200},
		{"form raw decoding precedes escapes", "application/x-www-form-urlencoded", "type=EMAIL&message=\xc3%A9", "Ã�", "", 200},
		{"form raw invalid UTF8 fallback", "application/x-www-form-urlencoded", "type=EMAIL&message=\xff", "ÿ", "", 200},
		{"form escape before raw invalid", "application/x-www-form-urlencoded", "type=EMAIL&message=%C3\xa9", "�©", "", 200},
		{"form charset keys", "application/x-www-form-urlencoded; charset=latin1", "type=EMAIL&message=x&%73ubject=%E9", "x", "", 200},
		{"json invalid byte", "application/json", "{\"type\":\"EMAIL\",\"message\":\"\xff\"}", "", `"detail"`, 400},
		{"json latin1", "application/json; charset=iso-8859-1", "{\"type\":\"EMAIL\",\"message\":\"\xe9\"}", "é", "", 200},
		{"json replacement", "application/json", `{"type":"EMAIL","message":"�"}`, "�", "", 200},
		{"json surrogate", "application/json", `{"type":"EMAIL","message":"\ud800"}`, "", `"msg":"message not valid."`, 400},
		{"json paired surrogate", "application/json", `{"type":"EMAIL","message":"\ud83d\ude00"}`, "😀", "", 200},
		{"json ignored surrogate", "application/json", `{"type":"EMAIL","message":"ok","ignored":"\ud800"}`, "ok", "", 200},
		{"integer 4300", "application/json", `{"type":"EMAIL","message":` + digits[:4300] + `}`, digits[:4300], "", 200},
		{"integer 4301", "application/json", `{"type":"EMAIL","message":` + digits + `}`, "", `"detail":"JSON parse error - Exceeds the limit (4300 digits)`, 400},
		{"ignored integer 4300", "application/json", `{"type":"EMAIL","message":"ok","ignored":` + digits[:4300] + `}`, "ok", "", 200},
		{"ignored integer 4301", "application/json", `{"type":"EMAIL","message":"ok","ignored":` + digits + `}`, "", `"detail":"JSON parse error - Exceeds the limit (4300 digits)`, 400},
		{"overwritten integer 4301", "application/json", `{"type":"EMAIL","message":"ok","ignored":` + digits + `,"ignored":0}`, "", `"detail"`, 400},
		{"nested negative integer 4301", "application/json", `{"type":"EMAIL","message":"ok","ignored":[-` + digits + `]}`, "", `"detail"`, 400},
		{"quoted digits unrestricted", "application/json", `{"type":"EMAIL","message":"` + digits + `"}`, digits, "", 200},
		{"float digits unrestricted", "application/json", `{"type":"EMAIL","message":"ok","ignored":` + digits + `.0}`, "ok", "", 200},
	} {
		t.Run(tc.name, func(t *testing.T) {
			owner := "a1000000-0000-4000-8000-000000700001"
			accounts := messageReadsAccountsTest{owner: {ID: owner, Role: domain.RoleAssociation, Active: true}}
			fake := &messageMutationsHTTPFake{}
			h := NewMessageMutationsHandler(fake, service.NewActorAuthentication(accounts, &messageReadsTokensTest{sub: owner}), service.NewIdentityResolver(accounts, nil, nil, service.NewPermissions(), nil), func(*http.Request) (domain.LoginAuditContext, error) { return domain.LoginAuditContext{}, nil }, paymentcategorylifecycle.New())
			r := httptest.NewRequest("POST", "/communications/messages/add", strings.NewReader(tc.body))
			r.Header.Set("Authorization", "Bearer valid")
			r.Header.Set("Content-Type", tc.media)
			e := httptest.NewRecorder()
			messageMutationsTestRouter(h).ServeHTTP(e, r)
			if e.Code != tc.status || !strings.Contains(e.Body.String(), tc.contains) {
				t.Fatalf("response %d %.300s", e.Code, e.Body.String())
			}
			if tc.status == 200 {
				if fake.added != 1 || fake.input.Message != tc.message {
					t.Fatalf("add=%d message=%q", fake.added, fake.input.Message)
				}
				if tc.name == "form charset keys" && (fake.input.Subject == nil || *fake.input.Subject != "é") {
					t.Fatalf("subject %#v", fake.input.Subject)
				}
			} else if fake.added != 0 {
				t.Fatal("parser rejection reached persistence")
			}
		})
	}
}
