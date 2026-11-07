package handler

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/url"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type PaymentReadsHandler struct {
	payments   *service.PaymentReads
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewPaymentReadsHandler(payments *service.PaymentReads, actors *service.ActorAuthentication, identities *service.IdentityResolver) *PaymentReadsHandler {
	return &PaymentReadsHandler{payments: payments, actors: actors, identities: identities}
}

// paymentReadsPlain turns ordered request objects into maps for encoding/json.
func paymentReadsPlain(value any) any {
	switch typed := value.(type) {
	case domain.RequestObject:
		result := make(map[string]any, len(typed))
		for _, field := range typed {
			result[field.Name] = paymentReadsPlain(field.Value)
		}
		return result
	case map[string]any:
		result := make(map[string]any, len(typed))
		for key, item := range typed {
			result[key] = paymentReadsPlain(item)
		}
		return result
	case []any:
		result := make([]any, len(typed))
		for i, item := range typed {
			result[i] = paymentReadsPlain(item)
		}
		return result
	case []map[string]any:
		result := make([]any, len(typed))
		for i, item := range typed {
			result[i] = paymentReadsPlain(item)
		}
		return result
	}
	return value
}

// serve dispatches one @api_view(['GET']) view (no docstring).
func (h *PaymentReadsHandler) serve(c echo.Context, operation, name string, read func(service.PaymentReadsRequest) (any, error)) error {
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
	body, err := read(service.PaymentReadsRequest{Identity: *identity, Query: parseReferenceForm(request.URL.RawQuery),
		GroupHeader: request.Header.Get("X-Group-Id")})
	var invalid *service.PaymentInfoUUIDError
	var denied *domain.PermissionDenied
	switch {
	case errors.As(err, &invalid):
		return c.JSON(400, []string{invalid.Detail})
	case errors.As(err, &denied):
		return restError(c, denied)
	case errors.Is(err, domain.ErrPaymentReadsAthlete):
		// Response(status=HTTP_401_UNAUTHORIZED): no body and no challenge.
		return c.NoContent(401)
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

// List mirrors payment_list.
func (h *PaymentReadsHandler) List(c echo.Context) error {
	return h.serve(c, "payment_list", "Payment List", func(request service.PaymentReadsRequest) (any, error) {
		return h.payments.List(c.Request().Context(), request)
	})
}

// Stats mirrors payment_stats.
func (h *PaymentReadsHandler) Stats(c echo.Context) error {
	return h.serve(c, "payment_stats", "Payment Stats", func(request service.PaymentReadsRequest) (any, error) {
		return h.payments.Stats(c.Request().Context(), request)
	})
}

// SubscriptionPayments mirrors subscription_payments; the str converter receives the decoded segment.
func (h *PaymentReadsHandler) SubscriptionPayments(c echo.Context) error {
	uid, err := url.PathUnescape(c.Param("uid"))
	if err != nil {
		uid = c.Param("uid")
	}
	return h.serve(c, "subscription_payments", "Subscription Payments", func(request service.PaymentReadsRequest) (any, error) {
		return h.payments.SubscriptionPayments(c.Request().Context(), request, uid)
	})
}
