package handler

import (
	"context"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type RetiredBilling interface {
	AssociationTombstone(context.Context, domain.ResolvedIdentity) (bool, error)
}
type RetiredBillingHandler struct {
	billing    RetiredBilling
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewRetiredBillingHandler(billing RetiredBilling, actors *service.ActorAuthentication, identities *service.IdentityResolver) *RetiredBillingHandler {
	return &RetiredBillingHandler{billing: billing, actors: actors, identities: identities}
}

func (h *RetiredBillingHandler) ActivePlan(c echo.Context) error { return h.reply(c, false) }
func (h *RetiredBillingHandler) Checkout(c echo.Context) error   { return h.reply(c, true) }

func (h *RetiredBillingHandler) reply(c echo.Context, checkout bool) error {
	method, operation, name := http.MethodGet, "billing_active_plan", "Billing Active Plan"
	message := "Software subscriptions are no longer required."
	if checkout {
		method, operation, name = http.MethodPost, "billing_checkout", "Billing Checkout"
		message = "Platform billing checkout is disabled for self-hosted instances."
	}
	identity, err := authenticatedRESTIdentity(c, h.actors, h.identities, method, operation)
	if identity == nil {
		return err
	}
	r := c.Request()
	if r.Method == http.MethodOptions {
		return c.JSON(200, map[string]any{"name": name, "description": "", "renders": []string{"application/json"},
			"parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}})
	}
	if r.Method != method {
		return restError(c, &restInputError{Status: 405, Detail: restText(r, "method")})
	}
	allowed, err := h.billing.AssociationTombstone(r.Context(), *identity)
	if err != nil {
		return restError(c, err)
	}
	if !allowed {
		return c.JSON(http.StatusForbidden, dto.RetiredBilling{Message: "Info not available."})
	}
	return c.JSON(http.StatusGone, dto.RetiredBilling{Message: message})
}
