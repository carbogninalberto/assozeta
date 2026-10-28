package handler

import (
	"context"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type CarnetListService interface {
	List(context.Context, domain.ResolvedIdentity, string) (any, error)
}

type CarnetListHandler struct {
	lists      CarnetListService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewCarnetListHandler(lists CarnetListService, actors *service.ActorAuthentication, identities *service.IdentityResolver) *CarnetListHandler {
	return &CarnetListHandler{lists: lists, actors: actors, identities: identities}
}

func (h *CarnetListHandler) List(c echo.Context) error {
	identity, err := messageReadsIdentity(c, h.actors, h.identities, http.MethodGet, "carnet_list")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, "Carnet List")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	body, err := h.lists.List(request.Context(), *identity, request.Header.Get("X-Group-Id"))
	if err != nil {
		return restError(c, err)
	}
	return c.JSON(200, body)
}
