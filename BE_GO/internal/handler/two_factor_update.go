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

type TwoFactorUpdater interface {
	Update(context.Context, domain.ResolvedIdentity, dto.TwoFactorUpdateRequest, domain.LoginAuditContext) (bool, error)
}
type TwoFactorUpdateHandler struct {
	updates    TwoFactorUpdater
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
}

func NewTwoFactorUpdateHandler(updates TwoFactorUpdater, actors *service.ActorAuthentication, identities *service.IdentityResolver, audit LoginAuditReader) *TwoFactorUpdateHandler {
	return &TwoFactorUpdateHandler{updates, actors, identities, audit}
}
func (h *TwoFactorUpdateHandler) Update(c echo.Context) error {
	identity, err := authenticatedRESTIdentity(c, h.actors, h.identities, http.MethodPost, "two_fa_update")
	if identity == nil {
		return err
	}
	r := c.Request()
	if r.Method == http.MethodOptions {
		return c.JSON(200, map[string]any{"name": "Two Fa Update", "description": "API endpoint to update the two fa secret\n:param request: data: { OTP, username, password }\n:return:", "renders": []string{"application/json"}, "parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}})
	}
	if r.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(r, "method")})
	}
	body, err := readAuthenticatedRESTBody(c)
	if err != nil {
		return restError(c, err)
	}
	input, err := dto.ParseTwoFactorUpdate(body)
	if err != nil {
		return restError(c, err)
	}
	audit, err := h.audit(r)
	if err != nil {
		return restError(c, err)
	}
	enabled, err := h.updates.Update(r.Context(), *identity, input, audit)
	if errors.Is(err, service.ErrTwoFactorOTP) {
		return c.JSON(400, map[string]any{"data": map[string]string{"msg": "Otp validation failed."}})
	}
	if err != nil {
		return restError(c, err)
	}
	message := "Disabled two factor authentication"
	if enabled {
		message = "Two factor authentication enabled."
	}
	return c.JSON(200, map[string]any{"data": map[string]string{"msg": message}})
}
