package handler

import (
	"encoding/json"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// PaymentMiscHandler serves the @api_view function views
// payment_simulation_partial_quotes, payment_simulation_partial_quotes_apply
// and payment_sign.
type PaymentMiscHandler struct {
	payments   *service.PaymentMisc
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewPaymentMiscHandler(payments *service.PaymentMisc, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *PaymentMiscHandler {
	return &PaymentMiscHandler{payments: payments, actors: actors, identities: identities, audit: audit, operations: operations}
}

func (h *PaymentMiscHandler) failure(c echo.Context, err error) error {
	var denied *domain.PermissionDenied
	var invalid *dto.MessageReadUUIDError
	var validation *domain.PaymentMiscValidation
	switch {
	case errors.As(err, &denied):
		return restError(c, err)
	case errors.As(err, &invalid):
		return messageReadsError(c, err)
	case errors.Is(err, domain.ErrPaymentMiscBadRequest):
		return c.JSON(400, map[string]string{"msg": "validation errors."})
	case errors.As(err, &validation):
		english := restLanguage(c.Request()) == "en"
		body := map[string][]string{}
		for field, codes := range validation.Fields {
			for _, code := range codes {
				message := "La firma è obbligatoria."
				if code != service.PaymentSignRequired {
					message = paymentCategoryValidationMessage(code, english)
				}
				body[field] = append(body[field], message)
			}
		}
		return c.JSON(400, body)
	}
	// Every other failure (including refused, not ported paths) is the 500 page.
	return restError(c, domain.ErrPaymentMiscServer)
}

// entry resolves the identity and answers OPTIONS and other methods; nil
// identity means the response was written.
func (h *PaymentMiscHandler) entry(c echo.Context, verb, operation, name string) (*domain.ResolvedIdentity, error) {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, verb, operation)
	if identity == nil {
		return nil, err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return nil, supplierOptions(c, name, "")
	case verb:
		return identity, nil
	}
	return nil, restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
}

func (h *PaymentMiscHandler) request(c echo.Context, write *restWrite) service.PaymentMiscRequest {
	request := service.PaymentMiscRequest{Query: parseReferenceForm(c.Request().URL.RawQuery), GroupHeader: c.Request().Header.Get("X-Group-Id"),
		Language: djangoReadRouteLocale{}.Language(c.Request()), Authorization: requestAuthorization(c.Request())}
	if write != nil {
		request.Body, request.Audit = write.Body, write.Audit
	}
	return request
}

// Simulation mirrors payment_simulation_partial_quotes.
func (h *PaymentMiscHandler) Simulation(c echo.Context) error {
	identity, err := h.entry(c, http.MethodGet, "payment_simulation_partial_quotes", "Payment Simulation Partial Quotes")
	if identity == nil {
		return err
	}
	result, err := h.payments.Simulation(c.Request().Context(), *identity, h.request(c, nil))
	if err != nil {
		return h.failure(c, err)
	}
	encoded, err := json.Marshal(paymentReadsPlain(result))
	if err != nil {
		return h.failure(c, err)
	}
	return c.Blob(http.StatusOK, echo.MIMEApplicationJSON, encoded)
}

// Apply mirrors payment_simulation_partial_quotes_apply (a GET that writes).
func (h *PaymentMiscHandler) Apply(c echo.Context) error {
	identity, err := h.entry(c, http.MethodGet, "payment_simulation_partial_quotes_apply", "Payment Simulation Partial Quotes Apply")
	if identity == nil {
		return err
	}
	admitted, release, err := admitRESTWrite(c, h.operations, h.audit)
	if admitted == nil {
		return err
	}
	defer release()
	if err := h.payments.Apply(admitted.Context, *identity, h.request(c, admitted)); err != nil {
		return h.failure(c, err)
	}
	return c.JSON(http.StatusOK, map[string]string{"msg": "Partial quotes applied."})
}

// Sign mirrors payment_sign.
func (h *PaymentMiscHandler) Sign(c echo.Context) error {
	identity, err := h.entry(c, http.MethodPost, "payment_sign", "Payment Sign")
	if identity == nil {
		return err
	}
	admitted, release, err := admitRESTWrite(c, h.operations, h.audit)
	if admitted == nil {
		return err
	}
	defer release()
	result, err := h.payments.Sign(admitted.Context, *identity, h.request(c, admitted))
	if err != nil {
		return h.failure(c, err)
	}
	return c.JSON(http.StatusOK, result)
}
