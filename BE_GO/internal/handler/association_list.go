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

type AssociationListService interface {
	List(context.Context, domain.ResolvedIdentity) ([]dto.AssociationListItem, error)
}

type AssociationListHandler struct {
	associations AssociationListService
	actors       *service.ActorAuthentication
	identities   *service.IdentityResolver
}

func NewAssociationListHandler(associations AssociationListService, actors *service.ActorAuthentication, identities *service.IdentityResolver) *AssociationListHandler {
	return &AssociationListHandler{associations: associations, actors: actors, identities: identities}
}

func (h *AssociationListHandler) List(c echo.Context) error {
	identity, err := messageReadsIdentity(c, h.actors, h.identities, http.MethodGet, "sport_association_list")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return c.JSON(200, map[string]any{"name": "Sport Association List", "description": "List all sport associations",
			"renders": []string{"application/json"},
			"parses":  []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}})
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	items, err := h.associations.List(request.Context(), *identity)
	if errors.Is(err, service.ErrAssociationListNotSuperuser) {
		return c.NoContent(401)
	}
	if err != nil {
		return restError(c, err)
	}
	return c.JSON(200, items)
}
