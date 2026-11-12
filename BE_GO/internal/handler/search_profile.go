package handler

import (
	"context"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type SearchProfileService interface {
	Profile(context.Context, string, bool) (map[string]any, error)
}

type SearchProfileHandler struct {
	profiles   SearchProfileService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewSearchProfileHandler(profiles SearchProfileService, actors *service.ActorAuthentication, identities *service.IdentityResolver) *SearchProfileHandler {
	return &SearchProfileHandler{profiles: profiles, actors: actors, identities: identities}
}

// Profile is public (AllowAny): an authenticated caller still passes identity
// resolution, including the scoped association-username check.
func (h *SearchProfileHandler) Profile(c echo.Context) error {
	r := c.Request()
	c.Response().Header().Set("Allow", "GET, OPTIONS")
	c.Response().Header().Set("Content-Language", restLanguage(r))
	query := parseReferenceForm(r.URL.RawQuery)
	if format, ok := query.Lookup("format"); ok && format != "" && format != "json" {
		return restError(c, &restInputError{Status: 404, Detail: restText(r, "notfound")})
	}
	if !acceptsJSON(r.Header.Get("Accept")) {
		return restError(c, &restInputError{Status: 406, Detail: restText(r, "accept")})
	}
	actor, err := h.actors.Authenticate(r.Context(), r.Header.Get("Authorization"))
	if err != nil {
		return restError(c, err)
	}
	username := c.Param("username")
	if actor != nil {
		var body any
		var bodyErr error
		if r.Header.Get("X-Impersonation-Id") != "" || r.Header.Get("User-Id") != "" {
			body, bodyErr = readAuthenticatedRESTBody(c)
		}
		path := domain.RequestObject{domain.RequestField{Name: "username", Value: username}}
		if _, err = h.identities.Resolve(r.Context(), actor, domain.IdentityRequest{Method: r.Method, Path: r.URL.Path,
			SessionID: r.Header.Get("X-Impersonation-Id"), TargetID: r.Header.Get("User-Id"), GroupID: r.Header.Get("X-Group-Id"),
			References: &domain.RequestReferences{Operation: "search_profile", Path: path, Query: query, Body: body, BodyError: bodyErr}}); err != nil {
			return restError(c, err)
		}
	}
	if r.Method == http.MethodOptions {
		return supplierOptions(c, "Search Profile", "Returns information about a certain profile")
	}
	if r.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(r, "method")})
	}
	_, moduleInfo := query.Lookup("module_info")
	body, err := h.profiles.Profile(r.Context(), username, moduleInfo)
	switch {
	case errors.Is(err, service.ErrSearchProfileNotFound):
		return c.JSON(404, map[string]string{"detail": "user not found."})
	case err != nil:
		return restError(c, err)
	}
	return c.JSON(200, body)
}
