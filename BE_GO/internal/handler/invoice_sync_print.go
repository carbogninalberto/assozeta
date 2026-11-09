package handler

import (
	"context"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type InvoiceSyncUpdateService interface {
	Update(ctx context.Context, identity domain.ResolvedIdentity, request service.InvoiceSyncUpdateRequest) (map[string]any, error)
}

// InvoiceSyncPrintHandler serves the @api_view(['PATCH']) function view
// invoice_update with payment-state's identity, admission and error mapping.
type InvoiceSyncPrintHandler struct {
	states  *PaymentStatesHandler
	updates InvoiceSyncUpdateService
}

func NewInvoiceSyncPrintHandler(states *PaymentStatesHandler, updates InvoiceSyncUpdateService) *InvoiceSyncPrintHandler {
	return &InvoiceSyncPrintHandler{states: states, updates: updates}
}

func (h *InvoiceSyncPrintHandler) Update(c echo.Context) error {
	identity, err := h.states.entry(c, http.MethodPatch, "invoice_update", "Invoice Update", "")
	if identity == nil {
		return err
	}
	// is_valid_uuid runs before request.data.
	if _, err := service.PaymentStateUUID(c.Param("uid")); err != nil {
		return h.states.failure(c, err)
	}
	admitted, release, err := admitRESTWrite(c, h.states.operations, h.states.audit)
	if admitted == nil {
		return err
	}
	defer release()
	request := c.Request()
	result, err := h.updates.Update(admitted.Context, *identity, service.InvoiceSyncUpdateRequest{UID: c.Param("uid"), Body: admitted.Body,
		Group: request.Header.Get("X-Group-Id"), Authorization: requestAuthorization(request), Audit: admitted.Audit})
	if err != nil {
		return h.states.failure(c, err)
	}
	return c.JSON(http.StatusOK, result)
}
