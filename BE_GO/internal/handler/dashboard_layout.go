package handler

import (
	"context"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type DashboardLayoutService interface {
	Set(context.Context, domain.ResolvedIdentity, any, domain.LoginAuditContext) error
}

type DashboardLayoutHandler struct {
	layouts    DashboardLayoutService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewDashboardLayoutHandler(layouts DashboardLayoutService, actors *service.ActorAuthentication, identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *DashboardLayoutHandler {
	return &DashboardLayoutHandler{layouts: layouts, actors: actors, identities: identities, audit: audit, operations: operations}
}

func (h *DashboardLayoutHandler) Set(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "statistic_dashboard_layout")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, "Statistic Dashboard Layout")
	}
	if request.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	body, err := readMessageMutationsBody(c)
	if err != nil {
		return restError(c, err)
	}
	audit, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	if err = h.layouts.Set(ctx, *identity, body, audit); err != nil {
		return messageReadsError(c, err)
	}
	return c.JSON(200, map[string]string{"data": "dashboard layout updated"})
}
