package handler

import (
	"context"
	"errors"
	"net/http"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type CollaboratorListReader interface {
	Read(context.Context, domain.ResolvedIdentity, *string) (any, error)
}
type CollaboratorListHandler struct {
	reader     CollaboratorListReader
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewCollaboratorListHandler(reader CollaboratorListReader, actors *service.ActorAuthentication, identities *service.IdentityResolver) *CollaboratorListHandler {
	return &CollaboratorListHandler{reader, actors, identities}
}
func (h *CollaboratorListHandler) Read(c echo.Context) error {
	original := c.Request()
	request := original.Clone(original.Context())
	for key, values := range original.Header {
		request.Header.Set(key, strings.Join(values, ","))
	}
	c.SetRequest(request)
	defer c.SetRequest(original)
	return ManualResponseEncoding()(h.dispatch)(c)
}
func (h *CollaboratorListHandler) dispatch(c echo.Context) error {
	r := c.Request()
	c.Response().Header().Set("Allow", "GET, OPTIONS")
	c.Response().Header().Set("Content-Language", collaboratorLanguage(r))
	// QueryDict counts raw fields, including duplicate keys and empty segments,
	// before negotiation, authentication or method dispatch.
	if r.URL.RawQuery != "" && strings.Count(r.URL.RawQuery, "&") >= 1000 {
		c.Response().Header().Del("Allow")
		return c.Blob(400, "text/html; charset=utf-8", []byte("\n<!doctype html>\n<html lang=\"en\">\n<head>\n  <title>Bad Request (400)</title>\n</head>\n<body>\n  <h1>Bad Request (400)</h1><p></p>\n</body>\n</html>\n"))
	}
	query := parseImpersonationQuery(r.URL.RawQuery)
	if format, present := query.Lookup("format"); present && format != "" && format != "json" {
		return restError(c, &restInputError{Status: 404, Detail: collaboratorText(r, "notfound")})
	}
	if !acceptsJSON(r.Header.Get("Accept")) {
		return restError(c, &restInputError{Status: 406, Detail: collaboratorText(r, "accept")})
	}
	actor, err := h.actors.Authenticate(r.Context(), r.Header.Get("Authorization"))
	if err != nil {
		return restError(c, err)
	}
	var body any
	var bodyErr error
	if actor != nil && (r.Header.Get("X-Impersonation-Id") != "" || r.Header.Get("User-Id") != "") {
		body, bodyErr = readAuthenticatedRESTBody(c)
		var input *restInputError
		if errors.As(bodyErr, &input) && input.Status == http.StatusUnsupportedMediaType {
			bodyErr = &restInputError{Status: input.Status, Detail: collaboratorText(r, "media")}
		}
	}
	identity, err := h.identities.Resolve(r.Context(), actor, domain.IdentityRequest{Method: r.Method, Path: r.URL.Path, SessionID: r.Header.Get("X-Impersonation-Id"), TargetID: r.Header.Get("User-Id"), GroupID: r.Header.Get("X-Group-Id"), References: &domain.RequestReferences{Operation: "collaborators_list", Query: query, Body: body, BodyError: bodyErr}})
	if err != nil {
		return restError(c, err)
	}
	if identity.ScopeUser == nil {
		c.Response().Header().Set("WWW-Authenticate", `Bearer realm="api"`)
		return restError(c, &restInputError{Status: 401, Detail: collaboratorText(r, "credentials")})
	}
	if identity.ScopeUser.Role != domain.RoleAssociation && identity.ScopeUser.Role != domain.RoleCollaborator {
		detail := collaboratorText(r, "permission")
		return restError(c, &domain.PermissionDenied{Detail: detail})
	}
	if r.Method == http.MethodOptions {
		return c.JSON(200, map[string]any{"name": "Collaborators List", "description": "", "renders": []string{"application/json"}, "parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}})
	}
	if r.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: collaboratorText(r, "method")})
	}
	var identifier *string
	if value, present := query.Lookup("user_id"); present {
		text := value.(string)
		identifier = &text
	}
	result, err := h.reader.Read(r.Context(), identity, identifier)
	if err != nil {
		var invalid *service.PaymentInfoUUIDError
		if errors.As(err, &invalid) {
			return c.JSON(400, []string{invalid.Detail})
		}
		return restError(c, err)
	}
	return c.JSON(200, result)
}
