package handler

import (
	"context"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type SupplierInvoiceStatsService interface {
	Stats(context.Context, domain.ResolvedIdentity) (dto.SupplierInvoiceStats, error)
}

type SupplierInvoiceStatsHandler struct {
	stats      SupplierInvoiceStatsService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewSupplierInvoiceStatsHandler(stats SupplierInvoiceStatsService, actors *service.ActorAuthentication, identities *service.IdentityResolver) *SupplierInvoiceStatsHandler {
	return &SupplierInvoiceStatsHandler{stats: stats, actors: actors, identities: identities}
}

func (h *SupplierInvoiceStatsHandler) Stats(c echo.Context) error {
	identity, err := messageReadsIdentity(c, h.actors, h.identities, http.MethodGet, "invoice_suppliers_stats")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, "Invoice Suppliers Stats")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	stats, err := h.stats.Stats(request.Context(), *identity)
	if err != nil {
		return restError(c, err)
	}
	return c.JSON(200, stats)
}

type CustomerInvoiceStatsService interface {
	Stats(context.Context, domain.ResolvedIdentity) (dto.CustomerInvoiceStats, error)
}

type CustomerInvoiceStatsHandler struct {
	stats      CustomerInvoiceStatsService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewCustomerInvoiceStatsHandler(stats CustomerInvoiceStatsService, actors *service.ActorAuthentication, identities *service.IdentityResolver) *CustomerInvoiceStatsHandler {
	return &CustomerInvoiceStatsHandler{stats: stats, actors: actors, identities: identities}
}

func (h *CustomerInvoiceStatsHandler) Stats(c echo.Context) error {
	identity, err := messageReadsIdentity(c, h.actors, h.identities, http.MethodGet, "invoice_customers_stats")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, "Invoice Customers Stats")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	stats, err := h.stats.Stats(request.Context(), *identity)
	if err != nil {
		return restError(c, err)
	}
	return c.JSON(200, stats)
}
