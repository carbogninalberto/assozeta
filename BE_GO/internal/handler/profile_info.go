package handler

import (
	"context"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type ProfileInfoReader interface {
	Read(context.Context, domain.ResolvedIdentity) (*dto.ProfileInfo, error)
}
type ProfileInfoHandler struct {
	profiles   ProfileInfoReader
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewProfileInfoHandler(profiles ProfileInfoReader, actors *service.ActorAuthentication, identities *service.IdentityResolver) *ProfileInfoHandler {
	return &ProfileInfoHandler{profiles, actors, identities}
}
func (h *ProfileInfoHandler) Info(c echo.Context) error {
	identity, err := authenticatedRESTIdentity(c, h.actors, h.identities, http.MethodGet, "profile_info")
	if identity == nil {
		return err
	}
	if c.Request().Method == http.MethodOptions {
		return c.JSON(200, map[string]any{"name": "Profile Info", "description": "", "renders": []string{"application/json"}, "parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}})
	}
	if c.Request().Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(c.Request(), "method")})
	}
	result, err := h.profiles.Read(c.Request().Context(), *identity)
	if err != nil {
		return restError(c, err)
	}
	return c.JSON(200, result)
}
