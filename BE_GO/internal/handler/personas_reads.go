package handler

import (
	"errors"
	"net/http"
	"net/url"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type PersonasHandler struct {
	personas   *service.Personas
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	// writes serves the soft delete of DELETE on personas/<pk>/info.
	writes *PersonasWritesHandler
}

func NewPersonasHandler(personas *service.Personas, actors *service.ActorAuthentication, identities *service.IdentityResolver) *PersonasHandler {
	return &PersonasHandler{personas: personas, actors: actors, identities: identities}
}

// serve dispatches one AssociateViewSet.as_view({'get': action}) route. The
// GET mapping adds HEAD and the class's own delete() answers DELETE (pk is nil
// on the routes without one); the class has no docstring.
func (h *PersonasHandler) serve(c echo.Context, pk *string, read func(domain.ResolvedIdentity, domain.RequestObject, string) (any, error)) error {
	c.Response().Header().Set("Allow", "GET, HEAD, DELETE, OPTIONS")
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, "AssociateViewSet")
	if identity == nil {
		return err
	}
	request := c.Request()
	query, group := parseReferenceForm(request.URL.RawQuery), request.Header.Get("X-Group-Id")
	var body any
	switch request.Method {
	case http.MethodOptions:
		return supplierOptions(c, "Associate", "")
	case http.MethodGet, http.MethodHead:
		body, err = read(*identity, query, group)
	case http.MethodDelete:
		if pk != nil && h.writes != nil {
			return h.writes.softDelete(c, *identity, *pk)
		}
		err = h.personas.Delete(request.Context(), *identity, pk, query, group)
	default:
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	switch {
	case errors.Is(err, service.ErrPersonaNotSportAssociation):
		return restError(c, &domain.PermissionDenied{Detail: "User is not a sport association"})
	case errors.Is(err, service.ErrPersonaInvalidPK):
		return restError(c, &restInputError{Status: 404, Detail: restText(request, "notfound")})
	case errors.Is(err, domain.ErrPersonaNotFound):
		return c.JSON(404, map[string]string{"detail": "No Associate matches the given query."})
	case errors.Is(err, dto.ErrDRFInvalidPage):
		return c.JSON(404, map[string]string{"detail": "Invalid page."})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.Blob(200, echo.MIMEApplicationJSON, dto.PythonJSONDumpsCompact(body))
}

func (h *PersonasHandler) List(c echo.Context) error {
	return h.serve(c, nil, func(identity domain.ResolvedIdentity, query domain.RequestObject, group string) (any, error) {
		return h.personas.List(c.Request().Context(), identity, query, group)
	})
}

func (h *PersonasHandler) All(c echo.Context) error {
	return h.serve(c, nil, func(identity domain.ResolvedIdentity, _ domain.RequestObject, group string) (any, error) {
		return h.personas.All(c.Request().Context(), identity, group)
	})
}

func (h *PersonasHandler) AllTutors(c echo.Context) error {
	return h.serve(c, nil, func(identity domain.ResolvedIdentity, _ domain.RequestObject, group string) (any, error) {
		return h.personas.AllTutors(c.Request().Context(), identity, group)
	})
}

func (h *PersonasHandler) AllWithSubscriptions(c echo.Context) error {
	return h.serve(c, nil, func(identity domain.ResolvedIdentity, _ domain.RequestObject, group string) (any, error) {
		return h.personas.AllWithSubscriptions(c.Request().Context(), identity, group)
	})
}

// Info serves personas/<str:pk>/info; the str converter receives the decoded segment.
func (h *PersonasHandler) Info(c echo.Context) error {
	pk, err := url.PathUnescape(c.Param("pk"))
	if err != nil {
		pk = c.Param("pk")
	}
	return h.serve(c, &pk, func(identity domain.ResolvedIdentity, query domain.RequestObject, group string) (any, error) {
		return h.personas.Info(c.Request().Context(), identity, pk, query, group)
	})
}
