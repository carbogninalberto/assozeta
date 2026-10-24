package handler

import (
	"context"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type ProfileUpdater interface {
	Update(context.Context, domain.ResolvedIdentity, dto.ProfileUpdateRequest, domain.LoginAuditContext) (*dto.ProfileUpdateResponse, error)
}
type ProfileUpdateHandler struct {
	updates    ProfileUpdater
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
}

func NewProfileUpdateHandler(updates ProfileUpdater, actors *service.ActorAuthentication, identities *service.IdentityResolver, audit LoginAuditReader) *ProfileUpdateHandler {
	return &ProfileUpdateHandler{updates, actors, identities, audit}
}
func (h *ProfileUpdateHandler) Update(c echo.Context) error {
	identity, err := authenticatedRESTIdentity(c, h.actors, h.identities, http.MethodPatch, "profile_update")
	if identity == nil {
		return err
	}
	r := c.Request()
	if r.Method == http.MethodOptions {
		return c.JSON(200, map[string]any{"name": "Profile Update", "description": "", "renders": []string{"application/json"}, "parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}})
	}
	if r.Method != http.MethodPatch {
		return restError(c, &restInputError{Status: 405, Detail: restText(r, "method")})
	}
	body, err := readAuthenticatedRESTBody(c)
	if err != nil {
		return restError(c, err)
	}
	input, err := dto.ParseProfileUpdate(body)
	if err != nil {
		return restError(c, err)
	}
	audit, err := h.audit(r)
	if err != nil {
		return restError(c, err)
	}
	result, err := h.updates.Update(r.Context(), *identity, input, audit)
	if err != nil {
		return restError(c, err)
	}
	return c.JSON(200, result)
}
