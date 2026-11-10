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

type SubscriptionReadsHandler struct {
	subscriptions *service.SubscriptionReads
	actors        *service.ActorAuthentication
	identities    *service.IdentityResolver
}

func NewSubscriptionReadsHandler(subscriptions *service.SubscriptionReads, actors *service.ActorAuthentication,
	identities *service.IdentityResolver) *SubscriptionReadsHandler {
	return &SubscriptionReadsHandler{subscriptions: subscriptions, actors: actors, identities: identities}
}

// serve dispatches one function view: allowed are its methods besides OPTIONS.
func (h *SubscriptionReadsHandler) serve(c echo.Context, operation, name, description string, allowed []string,
	read func(service.SubscriptionReadsRequest) (any, error)) error {
	if len(allowed) > 1 {
		c.Response().Header().Set("Allow", "GET, POST, OPTIONS")
	}
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	known := request.Method == http.MethodOptions
	for _, method := range allowed {
		known = known || request.Method == method
	}
	switch {
	case !known:
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	case request.Method == http.MethodOptions:
		return supplierOptions(c, name, description)
	}
	input := service.SubscriptionReadsRequest{Identity: *identity, Query: parseReferenceForm(request.URL.RawQuery),
		GroupHeader: request.Header.Get("X-Group-Id"), Method: request.Method}
	if values, present := request.Header[http.CanonicalHeaderKey("Authorization")]; present && len(values) > 0 {
		value := values[0]
		input.Authorization = &value
	}
	body, err := read(input)
	var invalid *service.PaymentInfoUUIDError
	var denied *domain.PermissionDenied
	switch {
	case errors.As(err, &invalid):
		return c.JSON(400, []string{invalid.Detail})
	case errors.As(err, &denied):
		return restError(c, denied)
	case errors.Is(err, domain.ErrSubscriptionReadsAthlete):
		// Response(status=HTTP_401_UNAUTHORIZED): no body and no challenge.
		return c.NoContent(401)
	case errors.Is(err, domain.ErrSubscriptionReadsNotFound):
		return c.JSON(404, map[string]string{"msg": "Subscription not found."})
	case errors.Is(err, domain.ErrSubscriptionReadsNotAllowed):
		return c.JSON(403, map[string]string{"msg": "not allowed"})
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

func subscriptionReadsUID(c echo.Context) string {
	// The str converter receives the decoded segment.
	uid, err := url.PathUnescape(c.Param("uid"))
	if err != nil {
		return c.Param("uid")
	}
	return uid
}

// List mirrors subscription_list (GET and POST).
func (h *SubscriptionReadsHandler) List(c echo.Context) error {
	return h.serve(c, "subscription_list", "Subscription List", "", []string{http.MethodGet, http.MethodPost},
		func(request service.SubscriptionReadsRequest) (any, error) {
			return h.subscriptions.List(c.Request().Context(), request)
		})
}

// ListAll mirrors subscription_list_all.
func (h *SubscriptionReadsHandler) ListAll(c echo.Context) error {
	return h.serve(c, "subscription_list_all", "Subscription List All", "", []string{http.MethodGet},
		func(request service.SubscriptionReadsRequest) (any, error) {
			return h.subscriptions.ListAll(c.Request().Context(), request)
		})
}

// Archived mirrors subscription_list_archived.
func (h *SubscriptionReadsHandler) Archived(c echo.Context) error {
	return h.serve(c, "subscription_list_archived", "Subscription List Archived", "", []string{http.MethodGet},
		func(request service.SubscriptionReadsRequest) (any, error) {
			return h.subscriptions.Archived(c.Request().Context(), request)
		})
}

// Info mirrors subscription_info.
func (h *SubscriptionReadsHandler) Info(c echo.Context) error {
	uid := subscriptionReadsUID(c)
	return h.serve(c, "subscription_info", "Subscription Info", "", []string{http.MethodGet},
		func(request service.SubscriptionReadsRequest) (any, error) {
			return h.subscriptions.Info(c.Request().Context(), request, uid)
		})
}

// Calendar mirrors subscription_calendar (its docstring is the OPTIONS description).
func (h *SubscriptionReadsHandler) Calendar(c echo.Context) error {
	uid := subscriptionReadsUID(c)
	return h.serve(c, "subscription_calendar", "Subscription Calendar", "API endpoint to get the attendance registry for the current course",
		[]string{http.MethodGet}, func(request service.SubscriptionReadsRequest) (any, error) {
			return h.subscriptions.Calendar(c.Request().Context(), request, uid)
		})
}
