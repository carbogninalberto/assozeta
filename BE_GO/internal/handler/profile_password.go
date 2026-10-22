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

type ProfilePasswordUpdater interface {
	Update(context.Context, domain.ResolvedIdentity, dto.ProfilePasswordRequest) (*dto.ProfilePasswordResponse, error)
}
type ProfilePasswordHandler struct {
	updates    ProfilePasswordUpdater
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewProfilePasswordHandler(updates ProfilePasswordUpdater, actors *service.ActorAuthentication, identities *service.IdentityResolver) *ProfilePasswordHandler {
	return &ProfilePasswordHandler{updates, actors, identities}
}
func (h *ProfilePasswordHandler) Update(c echo.Context) error {
	identity, err := authenticatedRESTIdentity(c, h.actors, h.identities, http.MethodPatch, "profile_update_password")
	if identity == nil {
		return err
	}
	if c.Request().Method == http.MethodOptions {
		return c.JSON(200, map[string]any{"name": "Profile Update Password", "description": "", "renders": []string{"application/json"}, "parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}})
	}
	if c.Request().Method != http.MethodPatch {
		return restError(c, &restInputError{Status: 405, Detail: restText(c.Request(), "method")})
	}
	body, err := readAuthenticatedRESTBody(c)
	if err != nil {
		return restError(c, err)
	}
	input, err := dto.ParseProfilePassword(body)
	if err != nil {
		return restError(c, err)
	}
	result, err := h.updates.Update(c.Request().Context(), *identity, input)
	if errors.Is(err, service.ErrProfilePasswordInvalid) {
		return c.JSON(400, map[string]string{"msg": "password not valid."})
	}
	if err != nil {
		return restError(c, err)
	}
	return c.JSON(200, result)
}
