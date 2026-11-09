package handler

import (
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// MiscExportsHandler serves subscription/list/export (@api_view(['POST', 'GET']))
// and invoice/list/export (@api_view(['GET'])).
type MiscExportsHandler struct {
	exports    *service.MiscExports
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewMiscExportsHandler(exports *service.MiscExports, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *MiscExportsHandler {
	return &MiscExportsHandler{exports: exports, actors: actors, identities: identities, audit: audit, operations: operations}
}

func miscExportsRespond(c echo.Context, body any, err error) error {
	if err != nil {
		// Unhandled view exceptions and the refused spreadsheet outputs are the 500 page.
		return messageReadsError(c, err)
	}
	return c.Blob(http.StatusOK, echo.MIMEApplicationJSON, dto.PythonJSONDumpsCompact(body))
}

// SubscriptionExport mirrors subscription_list_export. The full export may
// promote tutor relations (audited writes), so the request is admitted as a write.
func (h *MiscExportsHandler) SubscriptionExport(c echo.Context) error {
	c.Response().Header().Set("Allow", "GET, POST, OPTIONS")
	request := c.Request()
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, request.Method, "subscription_list_export")
	if identity == nil {
		return err
	}
	switch request.Method {
	case http.MethodOptions:
		return supplierOptions(c, "Subscription List Export", "")
	case http.MethodGet, http.MethodPost:
	default:
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
	body, err := h.exports.SubscriptionExport(ctx, service.MiscExportRequest{Identity: *identity, Query: parseReferenceForm(request.URL.RawQuery),
		GroupHeader: request.Header.Get("X-Group-Id"), Audit: audit})
	return miscExportsRespond(c, body, err)
}

// InvoiceExport mirrors invoice_list_export.
func (h *MiscExportsHandler) InvoiceExport(c echo.Context) error {
	request := c.Request()
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, "invoice_list_export")
	if identity == nil {
		return err
	}
	switch request.Method {
	case http.MethodOptions:
		return supplierOptions(c, "Invoice List Export", "")
	case http.MethodGet:
	default:
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	body, err := h.exports.InvoiceExport(request.Context(), service.MiscExportRequest{Identity: *identity,
		Query: parseReferenceForm(request.URL.RawQuery), GroupHeader: request.Header.Get("X-Group-Id")})
	return miscExportsRespond(c, body, err)
}
