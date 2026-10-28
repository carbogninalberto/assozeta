package handler

import (
	"context"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type InstructorListService interface {
	List(context.Context, domain.ResolvedIdentity) (map[string]any, error)
}

type InstructorListHandler struct {
	lists      InstructorListService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewInstructorListHandler(lists InstructorListService, actors *service.ActorAuthentication, identities *service.IdentityResolver) *InstructorListHandler {
	return &InstructorListHandler{lists: lists, actors: actors, identities: identities}
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
