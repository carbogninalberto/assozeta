package handler

import (
	"context"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type IdentifierChecks interface {
	CheckEmail(context.Context, string, bool) (domain.IdentifierAvailability, error)
	CheckUsername(context.Context, string) (domain.IdentifierAvailability, error)
}
type IdentifierAvailabilityHandler struct {
	checks     IdentifierChecks
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewIdentifierAvailabilityHandler(checks IdentifierChecks, actors *service.ActorAuthentication, identities *service.IdentityResolver) *IdentifierAvailabilityHandler {
	return &IdentifierAvailabilityHandler{checks: checks, actors: actors, identities: identities}
}
func (h *IdentifierAvailabilityHandler) Email(c echo.Context) error    { return h.check(c, "email") }
func (h *IdentifierAvailabilityHandler) Username(c echo.Context) error { return h.check(c, "username") }

func (h *IdentifierAvailabilityHandler) check(c echo.Context, kind string) error {
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
	if actor != nil {
		var body any
		var bodyErr error
		if r.Header.Get("X-Impersonation-Id") != "" || r.Header.Get("User-Id") != "" {
			body, bodyErr = readReferenceBody(r)
		}
		_, err = h.identities.Resolve(r.Context(), actor, domain.IdentityRequest{Method: r.Method, Path: r.URL.Path,
			SessionID: r.Header.Get("X-Impersonation-Id"), TargetID: r.Header.Get("User-Id"), GroupID: r.Header.Get("X-Group-Id"),
			References: &domain.RequestReferences{Operation: "oauth2_check_" + kind, Query: query, Body: body, BodyError: bodyErr}})
		if err != nil {
			return restError(c, err)
		}
	}
	if r.Method == http.MethodOptions {
		name, description := "Oauth2 Check Username", "API endpoint to check if a username is already taken\n:param request:\n:return:"
		if kind == "email" {
			name = "Oauth2 Check Email"
			description = "API endpoint to check if an email is already taken\n:param request: data: {username, password} or {token}\n:return: token, refresh token"
		}
		return c.JSON(200, map[string]any{"name": name, "description": description, "renders": []string{"application/json"}, "parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}})
	}
	if r.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(r, "method")})
	}
	raw, present := query.Lookup(kind)
	value, ok := raw.(string)
	if !present || !ok {
		return restError(c, domain.ErrInvalidScopedRequest)
	}
	var result domain.IdentifierAvailability
	if kind == "email" {
		_, details := query.Lookup("get_user")
		result, err = h.checks.CheckEmail(r.Context(), value, details)
	} else {
		result, err = h.checks.CheckUsername(r.Context(), value)
	}
	if errors.Is(err, domain.ErrIdentifierTooLong) {
		return c.JSON(400, dto.IdentifierAvailability{Valid: false, Exception: "invalid payload."})
	}
	if err != nil {
		return restError(c, err)
	}
	response := dto.IdentifierAvailability{Valid: result.Available}
	status := http.StatusConflict
	if result.Available {
		status = http.StatusOK
		response.Data = &dto.IdentifierAvailableData{Message: kind + " is available."}
	} else {
		response.Exception = kind + " already taken."
	}
	if result.User != nil {
		response.User = &dto.IdentifierUser{FirstName: result.User.FirstName, LastName: result.User.LastName, Email: result.User.Email, ID: result.User.ID}
	}
	return c.JSON(status, response)
}
