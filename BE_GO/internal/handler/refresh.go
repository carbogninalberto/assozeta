package handler

import (
	"errors"
	"net/http"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/jwt"
	"github.com/labstack/echo/v4"
)

type RefreshHandler struct {
	refresh    *service.RefreshService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewRefreshHandler(refresh *service.RefreshService, actors *service.ActorAuthentication, identities *service.IdentityResolver) *RefreshHandler {
	return &RefreshHandler{refresh: refresh, actors: actors, identities: identities}
}

// Refresh is explicitly registered as a DRF-style CSRF-exempt endpoint. AllowAny
// still authenticates any supplied Bearer credential and applies identity scope.
func (h *RefreshHandler) Refresh(c echo.Context) error {
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
	bodyRead := false
	readBody := func() {
		if !bodyRead {
			body, bodyErr = readReferenceBody(r)
			bodyRead = true
		}
	}
	if actor != nil {
		// Parse failures are deferred inside the scoped guard until Django would
		// access request.data; method/operation/credential denials keep precedence.
		if r.Header.Get("X-Impersonation-Id") != "" || r.Header.Get("User-Id") != "" {
			readBody()
		}
		_, err = h.identities.Resolve(r.Context(), actor, domain.IdentityRequest{Method: r.Method, Path: r.URL.Path,
			SessionID: r.Header.Get("X-Impersonation-Id"), TargetID: r.Header.Get("User-Id"), GroupID: r.Header.Get("X-Group-Id"),
			References: &domain.RequestReferences{Operation: "oauth2_refresh_token", Query: query, Body: body, BodyError: bodyErr}})
		if err != nil {
			return restError(c, err)
		}
	}
	if r.Method == http.MethodOptions {
		return c.JSON(200, map[string]any{"name": "Oauth2 Refresh Token", "description": "API endpoint to refresh access token using JWT.\n\nRequest body:\n    {refresh_token: string}\n\nReturns:\n    {access_token, refresh_token, expires_in, token_type}", "renders": []string{"application/json"}, "parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}})
	}
	if r.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(r, "method")})
	}
	readBody()
	if bodyErr != nil {
		return restError(c, bodyErr)
	}
	var value any
	present := false
	switch data := body.(type) {
	case domain.RequestObject:
		value, present = data.Lookup("refresh_token")
	case []any:
		for _, item := range data {
			if text, ok := item.(string); ok && text == "refresh_token" {
				return restError(c, domain.ErrInvalidScopedRequest)
			}
		}
	case string:
		if strings.Contains(data, "refresh_token") {
			return restError(c, domain.ErrInvalidScopedRequest)
		}
	default:
		return restError(c, domain.ErrInvalidScopedRequest)
	}
	pair, err := h.refresh.Refresh(r.Context(), value, present)
	if err != nil {
		return restError(c, err)
	}
	c.SetCookie(&http.Cookie{Name: "BKN_AUTH", Value: pair.AccessToken, HttpOnly: true, Secure: true, SameSite: http.SameSiteStrictMode,
		Path: "/", MaxAge: int(jwt.AccessLifetime / time.Second), Expires: time.Now().Add(jwt.AccessLifetime).UTC()})
	return c.JSON(200, pair)
}

func restError(c echo.Context, err error) error {
	var input *restInputError
	var auth *domain.AuthenticationFailure
	var denied *domain.PermissionDenied
	switch {
	case errors.As(err, &input):
		return c.JSON(input.Status, map[string]any{"detail": input.Detail})
	case errors.As(err, &auth):
		c.Response().Header().Set("WWW-Authenticate", `Bearer realm="api"`)
		body := map[string]any{"detail": auth.Detail, "code": auth.Code}
		if auth.Code == "token_not_valid" && auth.Detail == "Given token not valid for any token type" {
			body["messages"] = []map[string]string{{"token_class": "AccessToken", "token_type": "access", "message": "Token is invalid or expired"}}
		}
		return c.JSON(401, body)
	case errors.As(err, &denied):
		body := map[string]any{"detail": denied.Detail}
		if denied.ImpersonationInvalid {
			body["impersonation_invalid"] = "True"
		}
		return c.JSON(403, body)
	case errors.Is(err, service.ErrRefreshRequired):
		return c.JSON(400, map[string]string{"msg": "refresh_token required"})
	case errors.Is(err, service.ErrRefreshInvalid):
		return c.JSON(401, map[string]string{"msg": "Invalid or expired refresh token"})
	default:
		// Never serialize provider errors or credentials into responses/logs.
		return c.HTML(500, "\n<!doctype html>\n<html lang=\"en\">\n<head>\n  <title>Server Error (500)</title>\n</head>\n<body>\n  <h1>Server Error (500)</h1><p></p>\n</body>\n</html>\n")
	}
}
