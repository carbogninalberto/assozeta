package handler

import (
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// authenticatedRESTIdentity performs transport admission for protected REST views.
// A nil identity means an error response was handled; the caller returns err.
// Business invariants, response DTOs, OPTIONS and method dispatch stay explicit
// in the endpoint handler. AllowAny routes have distinct anonymous-scope behavior.
// Write handlers must pass the returned actor/effective/scope to their audit services.
func authenticatedRESTIdentity(c echo.Context, actors *service.ActorAuthentication, identities *service.IdentityResolver, method, operation string) (*domain.ResolvedIdentity, error) {
	r := c.Request()
	c.Response().Header().Set("Allow", method+", OPTIONS")
	c.Response().Header().Set("Content-Language", restLanguage(r))
	if !acceptsJSON(r.Header.Get("Accept")) {
		return nil, restError(c, &restInputError{Status: 406, Detail: restText(r, "accept")})
	}
	query := parseReferenceForm(r.URL.RawQuery)
	if format, ok := query.Lookup("format"); ok && format != "json" {
		return nil, restError(c, &restInputError{Status: 404, Detail: restText(r, "notfound")})
	}
	actor, err := actors.Authenticate(r.Context(), r.Header.Get("Authorization"))
	if err != nil {
		return nil, restError(c, err)
	}
	var body any
	var bodyErr error
	if actor != nil && (r.Header.Get("X-Impersonation-Id") != "" || r.Header.Get("User-Id") != "") {
		body, bodyErr = readReferenceBody(r)
	}
	// IsAuthenticated resolves scope even for an anonymous request. Forged
	// impersonation therefore retains its denial before missing-credential 401.
	identity, err := identities.Resolve(r.Context(), actor, domain.IdentityRequest{Method: r.Method, Path: r.URL.Path,
		SessionID: r.Header.Get("X-Impersonation-Id"), TargetID: r.Header.Get("User-Id"), GroupID: r.Header.Get("X-Group-Id"),
		References: &domain.RequestReferences{Operation: operation, Query: query, Body: body, BodyError: bodyErr}})
	if err != nil {
		return nil, restError(c, err)
	}
	if identity.ScopeUser == nil {
		c.Response().Header().Set("WWW-Authenticate", `Bearer realm="api"`)
		return nil, restError(c, &restInputError{Status: 401, Detail: restText(r, "credentials")})
	}
	return &identity, nil
}
