package handler

import (
	"context"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type TwoFactorStatus interface {
	Status(context.Context, domain.ResolvedIdentity) (bool, error)
}
type TwoFactorInfoHandler struct {
	status     TwoFactorStatus
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewTwoFactorInfoHandler(status TwoFactorStatus, actors *service.ActorAuthentication, identities *service.IdentityResolver) *TwoFactorInfoHandler {
	return &TwoFactorInfoHandler{status: status, actors: actors, identities: identities}
}

func (h *TwoFactorInfoHandler) Info(c echo.Context) error {
	identity, err := authenticatedRESTIdentity(c, h.actors, h.identities, http.MethodGet, "two_fa_info")
	if identity == nil {
		return err
	}
	r := c.Request()
	if r.Method == http.MethodOptions {
		return c.JSON(200, map[string]any{"name": "Two Fa Info", "description": "This endpoint returns info about two factor authentication for the requesting user\n:param request:\n:return:",
			"renders": []string{"application/json"}, "parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}})
	}
	if r.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(r, "method")})
	}
	enabled, err := h.status.Status(r.Context(), *identity)
	if err != nil {
		return restError(c, err)
	}
	return c.JSON(200, dto.TwoFactorInfo{Data: dto.TwoFactorStatus{Enabled: enabled}})
}
