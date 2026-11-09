package handler

import (
	"bytes"
	"errors"
	"io"
	"net/http"
	"net/url"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// StripeRoutesHandler serves the stripe/* @api_view function views of
// BE/application/views/stripe_views.py (all excluded from the permission registry).
type StripeRoutesHandler struct {
	stripe     *service.StripeRoutes
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewStripeRoutesHandler(stripe *service.StripeRoutes, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *StripeRoutesHandler {
	return &StripeRoutesHandler{stripe: stripe, actors: actors, identities: identities, audit: audit, operations: operations}
}

const stripeDisabled = "Stripe Connect onboarding is disabled for self-hosted direct Stripe."

// entry resolves the identity (anonymous for AllowAny views) and answers
// OPTIONS and other methods; a nil identity means the response was written.
func (h *StripeRoutesHandler) entry(c echo.Context, verb, operation, name, description string, anonymous bool) (*domain.ResolvedIdentity, error) {
	identity, err := messageMutationsIdentityFor(c, h.actors, h.identities, verb, operation, anonymous)
	if identity == nil {
		return nil, err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return nil, supplierOptions(c, name, description)
	case verb:
		return identity, nil
	}
	return nil, restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
}

func (h *StripeRoutesHandler) disabled(c echo.Context, verb, operation, name, description string) error {
	identity, err := h.entry(c, verb, operation, name, description, false)
	if identity == nil {
		return err
	}
	return c.JSON(http.StatusGone, map[string]string{"error": stripeDisabled})
}

// Info, OnBoarding and CompleteOnBoarding are IsAuthenticated views that always answer 410.
func (h *StripeRoutesHandler) Info(c echo.Context) error {
	return h.disabled(c, http.MethodGet, "stripe_info", "Stripe Info", "This endpoint returns onboarding info\n:param request:\n:return:")
}

func (h *StripeRoutesHandler) OnBoarding(c echo.Context) error {
	return h.disabled(c, http.MethodPost, "stripe_on_boarding", "Stripe On Boarding",
		"This endpoint start the Stripe connection by prefilling the base info\n:param request:\n:return:")
}

func (h *StripeRoutesHandler) CompleteOnBoarding(c echo.Context) error {
	return h.disabled(c, http.MethodPost, "stripe_complete_on_boarding", "Stripe Complete On Boarding",
		"This endpoint updates the onboarding process\n:param request:\n:return:")
}

func (h *StripeRoutesHandler) failure(c echo.Context, err error) error {
	var denied *domain.PermissionDenied
	var invalid *dto.MessageReadUUIDError
	switch {
	case errors.As(err, &denied):
		return restError(c, err)
	case errors.As(err, &invalid):
		return messageReadsError(c, err)
	case errors.Is(err, service.ErrStripePaymentNotFound):
		return c.JSON(http.StatusNotFound, map[string]string{"detail": "payment not found"})
	case errors.Is(err, service.ErrStripeOffline):
		return c.JSON(http.StatusConflict, map[string]string{"error": "Online payments are not configured."})
	case errors.Is(err, service.ErrStripeNoPayments):
		return c.JSON(http.StatusBadRequest, map[string]any{"data": map[string]string{"msg": "no payments provided."}})
	case errors.Is(err, service.ErrStripeWebhookUnconfigured):
		return c.JSON(http.StatusServiceUnavailable, map[string]string{"error": "Stripe webhook is not configured."})
	case errors.Is(err, service.ErrStripeWebhookRejected):
		return c.NoContent(http.StatusBadRequest)
	}
	// Every other failure (including refused, not ported paths) is the 500 page.
	return restError(c, domain.ErrStripeServer)
}

// admitted tracks the write and reads the audit context without parsing the body.
func (h *StripeRoutesHandler) admitted(c echo.Context, run func(request service.StripeRequest) error) error {
	request := c.Request()
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
	c.SetRequest(request.WithContext(ctx))
	return run(service.StripeRequest{GroupHeader: request.Header.Get("X-Group-Id"), Audit: audit})
}

// Pay is stripe_pay (AllowAny).
func (h *StripeRoutesHandler) Pay(c echo.Context) error {
	identity, err := h.entry(c, http.MethodPost, "stripe_pay", "Stripe Pay",
		"This endpoint allows to pay by providing a payment id\n:payment_id, request:\n:return:", true)
	if identity == nil {
		return err
	}
	identifier := c.Param("payment_id")
	if c.Request().URL.RawPath != "" {
		if identifier, err = url.PathUnescape(identifier); err != nil {
			return restError(c, err)
		}
	}
	var intent *string
	// request.GET.get('payment_intent_id'): the last value.
	if value, present := parseReferenceForm(c.Request().URL.RawQuery).Lookup("payment_intent_id"); present {
		if text, ok := value.(string); ok {
			intent = &text
		}
	}
	return h.admitted(c, func(request service.StripeRequest) error {
		result, err := h.stripe.Pay(c.Request().Context(), *identity, request, identifier, intent)
		if err != nil {
			return h.failure(c, err)
		}
		return c.JSON(http.StatusOK, result)
	})
}

// MultiplePay is stripe_multiple_pay (AllowAny).
func (h *StripeRoutesHandler) MultiplePay(c echo.Context) error {
	identity, err := h.entry(c, http.MethodPost, "stripe_multiple_pay", "Stripe Multiple Pay", "", true)
	if identity == nil {
		return err
	}
	admitted, release, err := admitRESTWrite(c, h.operations, h.audit)
	if admitted == nil {
		return err
	}
	defer release()
	result, err := h.stripe.MultiplePay(admitted.Context, *identity,
		service.StripeRequest{GroupHeader: c.Request().Header.Get("X-Group-Id"), Audit: admitted.Audit}, admitted.Body)
	if err != nil {
		return h.failure(c, err)
	}
	return c.JSON(http.StatusOK, result)
}

// stripeWebhookBodyLimit is DATA_UPLOAD_MAX_MEMORY_SIZE.
const stripeWebhookBodyLimit = 262144000

// Webhook is stripe_webhook (AllowAny): the raw body is kept for the
// signature before any reference parsing reads the stream.
func (h *StripeRoutesHandler) Webhook(c echo.Context) error {
	request := c.Request()
	var payload []byte
	if request.Body != nil {
		raw, err := io.ReadAll(io.LimitReader(request.Body, stripeWebhookBodyLimit+1))
		if err != nil {
			return restError(c, err)
		}
		if len(raw) > stripeWebhookBodyLimit {
			// Django's RequestDataTooBig is not ported.
			return restError(c, domain.ErrStripeServer)
		}
		payload = raw
		request.Body = io.NopCloser(bytes.NewReader(raw))
	}
	identity, err := h.entry(c, http.MethodPost, "stripe_webhook", "Stripe Webhook", "", true)
	if identity == nil {
		return err
	}
	var signature *string
	// request.headers.get('stripe-signature'): repeated headers are joined with ','.
	if values, present := request.Header[http.CanonicalHeaderKey("Stripe-Signature")]; present && len(values) > 0 {
		joined := strings.Join(values, ",")
		signature = &joined
	}
	return h.admitted(c, func(stripeRequest service.StripeRequest) error {
		if err := h.stripe.Webhook(c.Request().Context(), *identity, stripeRequest, payload, signature); err != nil {
			return h.failure(c, err)
		}
		return c.NoContent(http.StatusOK)
	})
}
