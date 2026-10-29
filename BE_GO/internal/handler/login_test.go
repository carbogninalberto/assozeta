package handler

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/jwt"
	"github.com/labstack/echo/v4"
)

type loginActors struct {
	account *domain.Account
	err     error
	calls   int
}

func (a *loginActors) Authenticate(context.Context, string) (*domain.Account, error) {
	a.calls++
	return a.account, a.err
}

type loginIdentities struct {
	request domain.IdentityRequest
	err     error
	calls   int
}

func (i *loginIdentities) Resolve(_ context.Context, _ *domain.Account, r domain.IdentityRequest) (domain.ResolvedIdentity, error) {
	i.calls++
	i.request = r
	return domain.ResolvedIdentity{}, i.err
}

type loginRequests struct {
	request dto.LoginRequest
	audit   domain.LoginAuditContext
	result  *dto.LoginResponse
	err     error
	calls   int
}

func (s *loginRequests) LoginRequest(_ context.Context, r dto.LoginRequest, a domain.LoginAuditContext) (*dto.LoginResponse, error) {
	s.calls++
	s.request = r
	s.audit = a
	return s.result, s.err
}
func loginTransportFixture() (*LoginHandler, *loginActors, *loginIdentities, *loginRequests) {
	a := &loginActors{}
	i := &loginIdentities{}
	s := &loginRequests{result: &dto.LoginResponse{Pair: jwt.Pair{AccessToken: "synthetic-access", RefreshToken: "synthetic-refresh", ExpiresIn: 14400, TokenType: "Bearer"}}}
	reader := func(*http.Request) (domain.LoginAuditContext, error) {
		id := "session-actor"
		return domain.LoginAuditContext{ActorID: &id}, nil
	}
	return NewLoginHandler(s, a, i, reader), a, i, s
}
func loginHTTP(h *LoginHandler, method, body string, headers map[string]string) *httptest.ResponseRecorder {
	e := echo.New()
	e.Any("/oauth2/login", h.Login)
	r := httptest.NewRequest(method, "/oauth2/login", strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		r.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	e.ServeHTTP(w, r)
	return w
}
func TestLoginTransportBranchAndCookieAfterSuccess(t *testing.T) {
	for _, body := range []string{`{"username":"target","password":"p","otp":null}`, `{"username":"target","password":"p","token":null,"backend":"google"}`} {
		h, _, _, s := loginTransportFixture()
		w := loginHTTP(h, "POST", body, nil)
		if w.Code != 200 || s.calls != 1 || s.request.Identifier != "target" || s.audit.ActorID == nil || *s.audit.ActorID != "session-actor" {
			t.Fatal("successful transport", w.Code, s.request)
		}
		expected := "password"
		if strings.Contains(body, "backend") {
			expected = "social"
		}
		if s.request.Mode != expected || s.request.OTPPresent != (expected == "password") {
			t.Fatal("request branch/presence lost")
		}
		cookies := w.Result().Cookies()
		if len(cookies) != 1 {
			t.Fatal("missing cookie")
		}
		c := cookies[0]
		if c.Name != "BKN_AUTH" || c.Value != "synthetic-access" || !c.HttpOnly || !c.Secure || c.SameSite != http.SameSiteStrictMode || c.Path != "/" || c.MaxAge != 14400 {
			t.Fatal("cookie contract")
		}
	}
}
func TestLoginTransportFailuresNeverSetCookie(t *testing.T) {
	for _, tc := range []struct {
		err    error
		status int
		body   string
	}{
		{service.ErrLoginNotFound, 401, `{"error":"invalid credential (not found)"}`},
		{service.ErrLoginCredentials, 401, `{"error":"invalid credentials"}`},
		{service.ErrSocialToken, 401, `{"error":"Invalid social token"}`},
		{service.ErrSocialEmail, 401, `{"error":"Email mismatch"}`},
		{&service.UnsupportedSocialBackend{Value: "None"}, 400, `{"error":"Unsupported backend: None"}`},
		{service.ErrSocialIdentity, 500, "Server Error (500)"},
		{service.ErrOTPRequired, 401, `{"msg":"OTP code required."}`},
		{service.ErrOTPInvalid, 401, `{"msg":"OTP code not valid."}`},
		{errors.New("private provider diagnostic"), 500, "Server Error (500)"},
		{context.Canceled, 500, "Server Error (500)"},
	} {
		h, _, _, s := loginTransportFixture()
		s.err = tc.err
		w := loginHTTP(h, "POST", `{"username":"target","password":"p"}`, nil)
		if w.Code != tc.status || !strings.Contains(w.Body.String(), tc.body) || w.Header().Get("Set-Cookie") != "" || strings.Contains(w.Body.String(), "private") {
			t.Fatal("error response or credential leaked", w.Code)
		}
	}
	h, _, _, s := loginTransportFixture()
	s.result = nil
	if w := loginHTTP(h, "POST", `{"username":"target","password":"p"}`, nil); w.Code != 500 || w.Header().Get("Set-Cookie") != "" {
		t.Fatal("nil response admitted")
	}
}
func TestLoginTransportPrecedenceAndNoBusinessCalls(t *testing.T) {
	for _, tc := range []struct {
		name, method, body string
		headers            map[string]string
		status, authCalls  int
	}{
		{"negotiation before authentication", "POST", "{", map[string]string{"Accept": "text/html"}, 406, 0},
		{"unsupported method before parse", "GET", "{", nil, 405, 1},
		{"options before parse", "OPTIONS", "{", nil, 200, 1},
		{"malformed body", "POST", "{", nil, 400, 1},
		{"missing credentials", "POST", "{}", nil, 400, 1},
		{"token presence prevents password branch", "POST", `{"username":"u","password":"p","token":null}`, nil, 400, 1},
		{"nonobject mirrors view get failure", "POST", "[]", nil, 500, 1},
		{"unsupported media", "POST", "a=b", map[string]string{"Content-Type": "text/plain"}, 415, 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			h, a, i, s := loginTransportFixture()
			w := loginHTTP(h, tc.method, tc.body, tc.headers)
			if w.Code != tc.status || a.calls != tc.authCalls || i.calls != 0 || s.calls != 0 || w.Header().Get("Set-Cookie") != "" {
				t.Fatal("wrong precedence", w.Code, a.calls, i.calls, s.calls)
			}
		})
	}
	h, a, _, s := loginTransportFixture()
	a.err = &domain.AuthenticationFailure{Code: "bad_authorization_header", Detail: "bad bearer"}
	if w := loginHTTP(h, "POST", "{", nil); w.Code != 401 || s.calls != 0 || w.Header().Get("WWW-Authenticate") == "" {
		t.Fatal("authentication precedence")
	}
	h, a, i, s := loginTransportFixture()
	a.account = &domain.Account{ID: "actor"}
	i.err = &domain.PermissionDenied{Detail: "denied"}
	if w := loginHTTP(h, "POST", "{", map[string]string{"X-Impersonation-Id": "session"}); w.Code != 403 || s.calls != 0 || i.request.References.BodyError == nil || i.request.References.Operation != "oauth2_login" {
		t.Fatal("scope precedence or trusted operation")
	}
	h, _, _, s = loginTransportFixture()
	h.audit = func(*http.Request) (domain.LoginAuditContext, error) {
		return domain.LoginAuditContext{}, errors.New("unavailable")
	}
	if w := loginHTTP(h, "POST", `{"username":"u","password":"p"}`, nil); w.Code != 500 || s.calls != 0 {
		t.Fatal("audit admission failure")
	}
}
