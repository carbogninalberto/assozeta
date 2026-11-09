package handler

import (
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// PrintingGenerateHandler serves printing/generate (@api_view(['POST'])).
type PrintingGenerateHandler struct {
	printing   *service.PrintingGenerate
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewPrintingGenerateHandler(printing *service.PrintingGenerate, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *PrintingGenerateHandler {
	return &PrintingGenerateHandler{printing: printing, actors: actors, identities: identities, audit: audit, operations: operations}
}

// Generate mirrors printing_generate. A report may promote tutor relations
// (audited writes), so the request is admitted as a write.
func (h *PrintingGenerateHandler) Generate(c echo.Context) error {
	c.Response().Header().Set("Allow", "POST, OPTIONS")
	request := c.Request()
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, request.Method, "printing_generate")
	if identity == nil {
		return err
	}
	switch request.Method {
	case http.MethodOptions:
		return supplierOptions(c, "Printing Generate", "")
	case http.MethodPost:
	default:
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	response, err := h.printing.Generate(write.Context, service.PrintingGenerateRequest{Identity: *identity, Body: write.Body,
		GroupHeader: request.Header.Get("X-Group-Id"), Audit: write.Audit})
	if err != nil {
		if !errors.Is(err, domain.ErrPrintingGenerateServer) && !errors.Is(err, domain.ErrPrintingGenerateNotPorted) {
			c.Logger().Error(err)
		}
		// Unhandled view exceptions and the refused outputs (reportlab PDF) are the 500 page.
		return messageReadsError(c, err)
	}
	if response.Empty {
		return c.NoContent(response.Status)
	}
	return c.Blob(response.Status, echo.MIMEApplicationJSON, dto.PythonJSONDumpsCompact(response.Body))
}
