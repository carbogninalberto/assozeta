package handler

import (
	"context"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type InstructorListService interface {
	List(context.Context, domain.ResolvedIdentity) (map[string]any, error)
}

type InstructorInfoService interface {
	Info(context.Context, domain.ResolvedIdentity, string, string, bool) (map[string]any, error)
}

type InstructorListHandler struct {
	lists      InstructorListService
	infos      InstructorInfoService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewInstructorListHandler(lists InstructorListService, infos InstructorInfoService, actors *service.ActorAuthentication, identities *service.IdentityResolver) *InstructorListHandler {
	return &InstructorListHandler{lists: lists, infos: infos, actors: actors, identities: identities}
}

func (h *InstructorListHandler) List(c echo.Context) error {
	identity, err := messageReadsIdentity(c, h.actors, h.identities, http.MethodGet, "instructor_list")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, "Instructor List")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	body, err := h.lists.List(request.Context(), *identity)
	if err != nil {
		return messageReadsError(c, err)
	}
	return c.JSON(200, body)
}

func (h *InstructorListHandler) Info(c echo.Context) error {
	identity, err := messageReadsIdentity(c, h.actors, h.identities, http.MethodGet, "instructor_info")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, "Instructor Info")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	value, present := parseReferenceForm(request.URL.RawQuery).Lookup("date_range")
	dateRange, _ := value.(string)
	body, err := h.infos.Info(request.Context(), *identity, c.Param("uid"), dateRange, present)
	switch {
	case errors.Is(err, service.ErrInstructorNotFound):
		return c.JSON(404, map[string]string{"error": "instructor not found"})
	case errors.Is(err, service.ErrInstructorNotAllowed):
		return c.JSON(403, map[string]string{"error": "not allowed"})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, body)
}
