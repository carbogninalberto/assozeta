package handler

import (
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// PaymentExportHandler serves payment/list/export through the payment reads dispatch.
type PaymentExportHandler struct {
	reads  *PaymentReadsHandler
	export *service.PaymentExport
}

func NewPaymentExportHandler(export *service.PaymentExport, actors *service.ActorAuthentication,
	identities *service.IdentityResolver) *PaymentExportHandler {
	return &PaymentExportHandler{reads: &PaymentReadsHandler{actors: actors, identities: identities}, export: export}
}

// Export mirrors payment_list_export.
func (h *PaymentExportHandler) Export(c echo.Context) error {
	return h.reads.serve(c, "payment_list_export", "Payment List Export", func(request service.PaymentReadsRequest) (any, error) {
		return h.export.Export(c.Request().Context(), request)
	})
}
