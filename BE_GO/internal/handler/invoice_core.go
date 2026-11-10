package handler

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/url"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type InvoiceCoreService interface {
	List(context.Context, service.InvoiceCoreRequest) (any, error)
	Archived(context.Context, service.InvoiceCoreRequest) (any, error)
	BulkArchive(context.Context, service.InvoiceCoreRequest) error
	BulkDelete(context.Context, service.InvoiceCoreRequest) error
	Delete(context.Context, service.InvoiceCoreRequest, string) error
}

type InvoiceCoreHandler struct {
	invoices   InvoiceCoreService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewInvoiceCoreHandler(invoices InvoiceCoreService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *InvoiceCoreHandler {
	return &InvoiceCoreHandler{invoices: invoices, actors: actors, identities: identities, audit: audit, operations: operations}
}

func (h *InvoiceCoreHandler) answer(c echo.Context, body any, err error) error {
	request := c.Request()
	switch {
	case errors.Is(err, domain.ErrInvoiceCoreMissing):
		return c.JSON(404, map[string]string{"error": "Invoice not found"})
	case errors.Is(err, service.ErrInvoiceCoreForbidden):
		return restError(c, &domain.PermissionDenied{Detail: messageReadsPermissionDetail(request)})
	case errors.Is(err, dto.ErrDRFInvalidPage):
		detail := "Invalid page."
		if restLanguage(request) != "en" {
			detail = "Pagina non valida."
		}
		return c.JSON(404, map[string]string{"detail": detail})
	case err != nil:
		return messageReadsError(c, err)
	}
	encoded, err := json.Marshal(paymentReadsPlain(body))
	if err != nil {
		return messageReadsError(c, err)
	}
	return c.Blob(200, echo.MIMEApplicationJSON, encoded)
}

// read dispatches one @api_view(['GET']) view (no docstring).
func (h *InvoiceCoreHandler) read(c echo.Context, operation, name string, run func(context.Context, service.InvoiceCoreRequest) (any, error)) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return supplierOptions(c, name, "")
	case http.MethodGet:
	default:
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	body, err := run(request.Context(), service.InvoiceCoreRequest{Identity: *identity, Query: parseReferenceForm(request.URL.RawQuery),
		GroupHeader: request.Header.Get("X-Group-Id"), Authorization: requestAuthorization(request)})
	return h.answer(c, body, err)
}

// write dispatches one @api_view([method]) view (no docstring).
func (h *InvoiceCoreHandler) write(c echo.Context, method, operation, name string, message string,
	run func(context.Context, service.InvoiceCoreRequest) error) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, method, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, name, "")
	}
	if request.Method != method {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	admitted, release, err := admitRESTWrite(c, h.operations, h.audit)
	if admitted == nil {
		return err
	}
	defer release()
	err = run(admitted.Context, service.InvoiceCoreRequest{Identity: *identity, Body: admitted.Body, Form: admitted.Form,
		GroupHeader: request.Header.Get("X-Group-Id"), Audit: admitted.Audit})
	return h.answer(c, map[string]string{"message": message}, err)
}

// List mirrors invoice_list.
func (h *InvoiceCoreHandler) List(c echo.Context) error {
	return h.read(c, "invoice_list", "Invoice List", h.invoices.List)
}

// Archived mirrors invoice_list_archived.
func (h *InvoiceCoreHandler) Archived(c echo.Context) error {
	return h.read(c, "invoice_list_archived", "Invoice List Archived", h.invoices.Archived)
}

// BulkArchive mirrors invoice_bulk_archive.
func (h *InvoiceCoreHandler) BulkArchive(c echo.Context) error {
	return h.write(c, http.MethodPost, "invoice_bulk_archive", "Invoice Bulk Archive", "All Invoices archived", h.invoices.BulkArchive)
}

// BulkDelete mirrors invoice_bulk_delete.
func (h *InvoiceCoreHandler) BulkDelete(c echo.Context) error {
	return h.write(c, http.MethodDelete, "invoice_bulk_delete", "Invoice Bulk Delete", "All Invoices deleted", h.invoices.BulkDelete)
}

// Delete mirrors invoice_delete; the str converter receives the decoded segment.
func (h *InvoiceCoreHandler) Delete(c echo.Context) error {
	uid, err := url.PathUnescape(c.Param("uid"))
	if err != nil {
		uid = c.Param("uid")
	}
	return h.write(c, http.MethodPost, "invoice_delete", "Invoice Delete", "Invoice deleted",
		func(ctx context.Context, request service.InvoiceCoreRequest) error {
			return h.invoices.Delete(ctx, request, uid)
		})
}
