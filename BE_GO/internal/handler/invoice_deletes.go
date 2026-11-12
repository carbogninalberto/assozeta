package handler

import (
	"context"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type InvoiceDeleteService interface {
	Delete(context.Context, domain.ResolvedIdentity, domain.InvoiceDeleteKind, string, domain.LoginAuditContext) error
}

type InvoiceDeletesHandler struct {
	deletes    InvoiceDeleteService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewInvoiceDeletesHandler(deletes InvoiceDeleteService, actors *service.ActorAuthentication, identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *InvoiceDeletesHandler {
	return &InvoiceDeletesHandler{deletes: deletes, actors: actors, identities: identities, audit: audit, operations: operations}
}

func (h *InvoiceDeletesHandler) Supplier(c echo.Context) error {
	return h.serve(c, domain.SupplierInvoiceDelete, "invoice_suppliers_delete", "Invoice Suppliers Delete")
}

func (h *InvoiceDeletesHandler) Customer(c echo.Context) error {
	return h.serve(c, domain.CustomerInvoiceDelete, "invoice_customers_delete", "Invoice Customers Delete")
}

func (h *InvoiceDeletesHandler) serve(c echo.Context, kind domain.InvoiceDeleteKind, operation, name string) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodDelete, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, name)
	}
	if request.Method != http.MethodDelete {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	audit, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	if err = h.deletes.Delete(ctx, *identity, kind, c.Param("uid"), audit); err != nil {
		return messageReadsError(c, err)
	}
	return c.JSON(200, map[string]string{"message": "Invoice deleted"})
}
