package handler

import (
	"context"
	"errors"
	"net/http"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/jwt"
	"github.com/labstack/echo/v4"
)

type LoginRequestService interface {
	LoginRequest(context.Context, dto.LoginRequest, domain.LoginAuditContext) (*dto.LoginResponse, error)
}
type LoginActorAuthentication interface {
	Authenticate(context.Context, string) (*domain.Account, error)
}
type LoginIdentityResolution interface {
	Resolve(context.Context, *domain.Account, domain.IdentityRequest) (domain.ResolvedIdentity, error)
}

// LoginAuditReader is supplied by trusted request/session middleware. It must not
// derive the audit actor from the login body or the newly authenticated account.
type LoginAuditReader func(*http.Request) (domain.LoginAuditContext, error)

type LoginHandler struct {
	sessions   LoginRequestService
	actors     LoginActorAuthentication
	identities LoginIdentityResolution
	audit      LoginAuditReader
}

func NewLoginHandler(sessions LoginRequestService, actors LoginActorAuthentication, identities LoginIdentityResolution, audit LoginAuditReader) *LoginHandler {
	return &LoginHandler{sessions, actors, identities, audit}
}

const loginDescription = "API endpoint to login a user.\nSupports password auth and social auth (Google/Apple).\n\nRequest body:\n    Password login: {username, password} or {username, password, otp}\n    Social login: {username, token, backend} or {username, token, backend, otp}\n\nReturns:\n    {access_token, refresh_token, expires_in, token_type, role, user_data, ...}"

// Login is the transport component, not yet registered by cmd/server. Registration
// requires complete request admission/rate limits, audit middleware, concrete
// social verification clients and image processing. AllowAny still authenticates supplied credentials.
func (h *LoginHandler) Login(c echo.Context) error {
	r := c.Request()
	c.Response().Header().Set("Allow", "POST, OPTIONS")
	c.Response().Header().Set("Content-Language", restLanguage(r))
	if !acceptsJSON(r.Header.Get("Accept")) {
		return restError(c, &restInputError{Status: 406, Detail: restText(r, "accept")})
	}
	query := parseReferenceForm(r.URL.RawQuery)
	if format, ok := query.Lookup("format"); ok && format != "json" {
		return restError(c, &restInputError{Status: 404, Detail: restText(r, "notfound")})
	}
	actor, err := h.actors.Authenticate(r.Context(), r.Header.Get("Authorization"))
	if err != nil {
		return restError(c, err)
	}
	var body any
	var bodyErr error
	read := false
	readBody := func() {
		if !read {
			body, bodyErr = readReferenceBody(r)
			read = true
		}
	}
	if actor != nil {
		if r.Header.Get("X-Impersonation-Id") != "" || r.Header.Get("User-Id") != "" {
			readBody()
		}
		_, err = h.identities.Resolve(r.Context(), actor, domain.IdentityRequest{Method: r.Method, Path: r.URL.Path,
			SessionID: r.Header.Get("X-Impersonation-Id"), TargetID: r.Header.Get("User-Id"), GroupID: r.Header.Get("X-Group-Id"),
			References: &domain.RequestReferences{Operation: "oauth2_login", Query: query, Body: body, BodyError: bodyErr}})
		if err != nil {
			return restError(c, err)
		}
	}
	if r.Method == http.MethodOptions {
		return c.JSON(200, map[string]any{"name": "Oauth2 Login", "description": loginDescription, "renders": []string{"application/json"}, "parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}})
	}
	if r.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(r, "method")})
	}
	readBody()
	if bodyErr != nil {
		return restError(c, bodyErr)
	}
	request, err := dto.ParseLoginRequest(body)
	if errors.Is(err, dto.ErrLoginRequestInvalid) {
		return c.JSON(400, map[string]string{"msg": "Invalid request."})
	}
	if err != nil {
		return restError(c, err)
	}
	if h.audit == nil {
		return restError(c, domain.ErrInvalidScopedRequest)
	}
	audit, err := h.audit(r)
	if err != nil {
		return restError(c, err)
	}
	result, err := h.sessions.LoginRequest(r.Context(), request, audit)
	var backend *service.UnsupportedSocialBackend
	switch {
	case errors.Is(err, service.ErrLoginNotFound):
		return c.JSON(401, map[string]string{"error": "invalid credential (not found)"})
	case errors.Is(err, service.ErrLoginCredentials):
		return c.JSON(401, map[string]string{"error": "invalid credentials"})
	case errors.Is(err, service.ErrSocialToken):
		return c.JSON(401, map[string]string{"error": "Invalid social token"})
	case errors.Is(err, service.ErrSocialEmail):
		return c.JSON(401, map[string]string{"error": "Email mismatch"})
	case errors.As(err, &backend):
		return c.JSON(400, map[string]string{"error": "Unsupported backend: " + backend.Value})
	case errors.Is(err, service.ErrOTPRequired):
		return c.JSON(401, map[string]string{"msg": "OTP code required."})
	case errors.Is(err, service.ErrOTPInvalid):
		return c.JSON(401, map[string]string{"msg": "OTP code not valid."})
	case err != nil:
		return restError(c, err)
	}
	if err = r.Context().Err(); err != nil {
		return restError(c, err)
	}
	if result == nil || result.AccessToken == "" {
		return restError(c, domain.ErrInvalidScopedRequest)
	}
	c.SetCookie(&http.Cookie{Name: "BKN_AUTH", Value: result.AccessToken, HttpOnly: true, Secure: true, SameSite: http.SameSiteStrictMode, Path: "/", MaxAge: int(jwt.AccessLifetime / time.Second), Expires: time.Now().Add(jwt.AccessLifetime).UTC()})
	return c.JSON(200, result)
}
