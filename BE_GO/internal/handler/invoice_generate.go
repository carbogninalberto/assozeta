package handler

import (
	"context"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type InvoiceGenerateService interface {
	Generate(ctx context.Context, identity domain.ResolvedIdentity, request service.InvoiceGenerateRequest, body any) (map[string]any, error)
}

// InvoiceGenerateHandler serves the @api_view function view payment_generate_invoice
// with payment-state's identity, admission and error mapping.
type InvoiceGenerateHandler struct {
	states      *PaymentStatesHandler
	generations InvoiceGenerateService
}

func NewInvoiceGenerateHandler(states *PaymentStatesHandler, generations InvoiceGenerateService) *InvoiceGenerateHandler {
	return &InvoiceGenerateHandler{states: states, generations: generations}
}

func (h *InvoiceGenerateHandler) Generate(c echo.Context) error {
	identity, err := h.states.entry(c, http.MethodPost, "payment_generate_invoice", "Payment Generate Invoice", "")
	if identity == nil {
		return err
	}
	// is_valid_uuid runs before the association lookup and request.data.
	if _, err := service.PaymentStateUUID(c.Param("uid")); err != nil {
		return h.states.failure(c, err)
	}
	admitted, release, err := admitRESTWrite(c, h.states.operations, h.states.audit)
	if admitted == nil {
		return err
	}
	defer release()
	request := c.Request()
	result, err := h.generations.Generate(admitted.Context, *identity, service.InvoiceGenerateRequest{ID: c.Param("uid"),
		GroupHeader: request.Header.Get("X-Group-Id"), Authorization: requestAuthorization(request), Audit: admitted.Audit}, admitted.Body)
	if errors.Is(err, domain.ErrInvoiceGenerateSelect) {
		return c.JSON(http.StatusBadRequest, map[string]string{"error": "Seleziona l'associato."})
	}
	if err != nil {
		return h.states.failure(c, err)
	}
	return c.JSON(http.StatusOK, result)
}
