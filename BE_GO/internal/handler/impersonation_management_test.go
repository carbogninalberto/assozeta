package handler

import (
	"context"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type managementHandlerFake struct {
	body   any
	query  domain.RequestObject
	starts int
	deny   bool
}

func (f *managementHandlerFake) Admit(_ context.Context, actor *domain.Account, administrative bool, _ string) (bool, error) {
	if f.deny {
		return false, nil
	}
	if actor == nil && !administrative {
		return false, &domain.PermissionDenied{Detail: "Accesso non consentito."}
	}
	return actor != nil, nil
}

func TestImpersonationItalianGenericPermission(t *testing.T) {
	accounts := paymentInfoAccounts{domain.Account{ID: "a1000000-0000-4000-8000-000000220008", Active: true}}
	h := NewImpersonationManagementHandler(&managementHandlerFake{deny: true}, service.NewActorAuthentication(accounts, &paymentInfoTokens{}), service.NewIdentityResolver(accounts, nil, nil, service.NewPermissions(), nil), nil)
	e := echo.New()
	e.Any("/administration/impersonation", h.Session)
	r := httptest.NewRequest("GET", "/administration/impersonation", nil)
	r.Header.Set("Authorization", "Bearer valid")
	r.Header.Set("Accept-Language", "it")
	w := httptest.NewRecorder()
	e.ServeHTTP(w, r)
	if w.Code != 403 || !strings.Contains(w.Body.String(), "Non hai l'autorizzazione per eseguire questa azione.") {
		t.Fatal(w.Code, w.Body.String())
	}
}
func (f *managementHandlerFake) List(_ context.Context, _ *domain.Account, query domain.RequestObject) (dto.ImpersonationList, error) {
	f.query = query
	return dto.ImpersonationList{}, nil
}
func (*managementHandlerFake) Current(context.Context, *domain.Account, string) (dto.ImpersonationCurrent, error) {
	return dto.ImpersonationCurrent{}, nil
}
func (f *managementHandlerFake) Start(_ context.Context, _ *domain.Account, body any, _ string, _ domain.LoginAuditContext) (dto.ImpersonationStarted, error) {
	f.body = body
	f.starts++
	return dto.ImpersonationStarted{}, nil
}
func (*managementHandlerFake) End(context.Context, *domain.Account, string, domain.LoginAuditContext) error {
	return nil
}

func TestImpersonationTransportAdmissionAndDuplicateKeys(t *testing.T) {
	for _, tc := range []struct {
		name, path, method, body, media, token string
		status                                 int
	}{
		{"anonymous owner", "association", "GET", "", "", "", 403},
		{"anonymous admin", "administration", "GET", "", "", "", 401},
		{"head unsupported", "administration", "HEAD", "malformed", "application/json", "valid", 405},
		{"get ignores body", "administration", "GET", "malformed", "application/json", "valid", 200},
		{"delete ignores body", "administration", "DELETE", "malformed", "application/json", "valid", 204},
		{"duplicate JSON", "administration", "POST", `{"target_user_id":"first","target_user_id":"last"}`, "application/json", "valid", 201},
		{"duplicate form", "administration", "POST", "target_user_id=first&target_user_id=last", "application/x-www-form-urlencoded", "valid", 201},
	} {
		t.Run(tc.name, func(t *testing.T) {
			manager := &managementHandlerFake{}
			accounts := paymentInfoAccounts{domain.Account{ID: "a1000000-0000-4000-8000-000000220008", Active: true, Superuser: true}}
			h := NewImpersonationManagementHandler(manager, service.NewActorAuthentication(accounts, &paymentInfoTokens{}), service.NewIdentityResolver(accounts, nil, nil, service.NewPermissions(), nil), func(_ *http.Request) (domain.LoginAuditContext, error) { return domain.LoginAuditContext{}, nil })
			e := echo.New()
			e.Any("/"+tc.path+"/impersonation", h.Session)
			r := httptest.NewRequest(tc.method, "/"+tc.path+"/impersonation", strings.NewReader(tc.body))
			r.Header.Set("Content-Type", tc.media)
			if tc.token != "" {
				r.Header.Set("Authorization", "Bearer "+tc.token)
			}
			w := httptest.NewRecorder()
			e.ServeHTTP(w, r)
			if w.Code != tc.status {
				t.Fatal(w.Code, w.Body.String())
			}
			if tc.status == 201 {
				object, ok := manager.body.(domain.RequestObject)
				if !ok || len(object) != 1 || object[0].Value != "last" {
					t.Fatal("duplicate normalization differs", manager.body)
				}
			}
		})
	}
}

func TestImpersonationQueryDecoding(t *testing.T) {
	for _, tc := range []struct {
		raw  string
		want domain.RequestObject
	}{
		{"q=%FF", domain.RequestObject{{Name: "q", Value: "�"}}},
		{"q=%FF%FF", domain.RequestObject{{Name: "q", Value: "��"}}},
		{"q=%E2%82", domain.RequestObject{{Name: "q", Value: "�"}}},
		{"q=%E2%82%41", domain.RequestObject{{Name: "q", Value: "�A"}}},
		{"q=%FF%ZZ+%41%", domain.RequestObject{{Name: "q", Value: "�%ZZ A%"}}},
		{"q=first&%71=last", domain.RequestObject{{Name: "q", Value: "last"}}},
		{"%FF=first&%EF%BF%BD=last", domain.RequestObject{{Name: "�", Value: "last"}}},
		{"&q=&other=a=b&", domain.RequestObject{{Name: "q", Value: ""}, {Name: "other", Value: "a=b"}}},
	} {
		t.Run(tc.raw, func(t *testing.T) {
			got := parseImpersonationQuery(tc.raw)
			if !reflect.DeepEqual(got, tc.want) {
				t.Fatalf("decoded query = %#v, want %#v", got, tc.want)
			}
		})
	}
}

func TestImpersonationUsersNormalizesQueryBeforeService(t *testing.T) {
	manager := &managementHandlerFake{}
	accounts := paymentInfoAccounts{domain.Account{ID: "a1000000-0000-4000-8000-000000220008", Active: true, Superuser: true}}
	h := NewImpersonationManagementHandler(manager, service.NewActorAuthentication(accounts, &paymentInfoTokens{}), service.NewIdentityResolver(accounts, nil, nil, service.NewPermissions(), nil), nil)
	e := echo.New()
	e.Any("/administration/impersonation/users", h.Users)
	request := httptest.NewRequest("GET", "/administration/impersonation/users?q=first&%71=%E2%82%ZZ", nil)
	request.Header.Set("Authorization", "Bearer valid")
	response := httptest.NewRecorder()
	e.ServeHTTP(response, request)
	want := domain.RequestObject{{Name: "q", Value: "�%ZZ"}}
	if response.Code != 200 || !reflect.DeepEqual(manager.query, want) {
		t.Fatalf("status %d, query %#v", response.Code, manager.query)
	}
}
