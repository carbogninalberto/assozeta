package handler

import (
	"context"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type TwoFactorProvisioner interface {
	Setup(context.Context, domain.ResolvedIdentity) (domain.TwoFactorProvisioning, error)
}
type TwoFactorSetupHandler struct {
	setup      TwoFactorProvisioner
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewTwoFactorSetupHandler(setup TwoFactorProvisioner, actors *service.ActorAuthentication, identities *service.IdentityResolver) *TwoFactorSetupHandler {
	return &TwoFactorSetupHandler{setup: setup, actors: actors, identities: identities}
}
func (h *TwoFactorSetupHandler) Setup(c echo.Context) error {
	identity, err := authenticatedRESTIdentity(c, h.actors, h.identities, http.MethodGet, "two_fa_setup")
	if identity == nil {
		return err
	}
	r := c.Request()
	if r.Method == http.MethodOptions {
		return c.JSON(200, map[string]any{"name": "Two Fa Setup", "description": "API endpoint to setup a new two fa secret\n:param request: data: { OTP, username, password }\n:return:", "renders": []string{"application/json"}, "parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}})
	}
	if r.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(r, "method")})
	}
	value, err := h.setup.Setup(r.Context(), *identity)
	if err != nil {
		return restError(c, err)
	}
	return c.JSON(200, dto.TwoFactorSetup{Data: dto.TwoFactorProvisioning{QRCodeURI: value.QRCodeURI, Secret: value.Secret}})
}
