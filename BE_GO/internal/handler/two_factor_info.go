package handler

import (
	"context"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type TwoFactorStatus interface {
	Status(context.Context, domain.ResolvedIdentity) (bool, error)
}
type TwoFactorInfoHandler struct {
	status     TwoFactorStatus
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewTwoFactorInfoHandler(status TwoFactorStatus, actors *service.ActorAuthentication, identities *service.IdentityResolver) *TwoFactorInfoHandler {
	return &TwoFactorInfoHandler{status: status, actors: actors, identities: identities}
}

func (h *TwoFactorInfoHandler) Info(c echo.Context) error {
	r := c.Request()
	c.Response().Header().Set("Allow", "GET, OPTIONS")
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
	if actor != nil && (r.Header.Get("X-Impersonation-Id") != "" || r.Header.Get("User-Id") != "") {
		body, bodyErr = readReferenceBody(r)
	}
	// IsAuthenticated resolves scope even for an anonymous request. Forged
	// impersonation therefore retains its denial before missing-credential 401.
	identity, err := h.identities.Resolve(r.Context(), actor, domain.IdentityRequest{Method: r.Method, Path: r.URL.Path,
		SessionID: r.Header.Get("X-Impersonation-Id"), TargetID: r.Header.Get("User-Id"), GroupID: r.Header.Get("X-Group-Id"),
		References: &domain.RequestReferences{Operation: "two_fa_info", Query: query, Body: body, BodyError: bodyErr}})
	if err != nil {
		return restError(c, err)
	}
	if identity.ScopeUser == nil {
		c.Response().Header().Set("WWW-Authenticate", `Bearer realm="api"`)
		return restError(c, &restInputError{Status: 401, Detail: restText(r, "credentials")})
	}
	if r.Method == http.MethodOptions {
		return c.JSON(200, map[string]any{"name": "Two Fa Info", "description": "This endpoint returns info about two factor authentication for the requesting user\n:param request:\n:return:",
			"renders": []string{"application/json"}, "parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}})
	}
	if r.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(r, "method")})
	}
	enabled, err := h.status.Status(r.Context(), identity)
	if err != nil {
		return restError(c, err)
	}
	return c.JSON(200, dto.TwoFactorInfo{Data: dto.TwoFactorStatus{Enabled: enabled}})
}
