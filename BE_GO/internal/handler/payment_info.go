package handler

import (
	"errors"
	"net/http"
	"net/url"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type PaymentInfoHandler struct {
	info       *service.PaymentInfo
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewPaymentInfoHandler(info *service.PaymentInfo, actors *service.ActorAuthentication, identities *service.IdentityResolver) *PaymentInfoHandler {
	return &PaymentInfoHandler{info: info, actors: actors, identities: identities}
}

// Info is a public checkout read. AllowAny still authenticates a supplied
// credential and applies the existing identity and collaborator permission gates.
func (h *PaymentInfoHandler) Info(c echo.Context) error {
	r := c.Request()
	c.Response().Header().Set("Allow", "GET, OPTIONS")
	c.Response().Header().Set("Content-Language", restLanguage(r))
	query := parseReferenceForm(r.URL.RawQuery)
	if format, ok := query.Lookup("format"); ok && format != "" && format != "json" {
		return restError(c, &restInputError{Status: 404, Detail: restText(r, "notfound")})
	}
	if !acceptsJSON(r.Header.Get("Accept")) {
		return restError(c, &restInputError{Status: 406, Detail: restText(r, "accept")})
	}
	actor, err := h.actors.Authenticate(r.Context(), r.Header.Get("Authorization"))
	if err != nil {
		return restError(c, err)
	}
	var identity domain.ResolvedIdentity
	if actor != nil {
		var body any
		var bodyErr error
		if r.Header.Get("X-Impersonation-Id") != "" || r.Header.Get("User-Id") != "" {
			body, bodyErr = readReferenceBody(r)
		}
		identity, err = h.identities.Resolve(r.Context(), actor, domain.IdentityRequest{Method: r.Method, Path: r.URL.Path,
			SessionID: r.Header.Get("X-Impersonation-Id"), TargetID: r.Header.Get("User-Id"), GroupID: r.Header.Get("X-Group-Id"),
			References: &domain.RequestReferences{Operation: "payment_info", Query: query, Body: body, BodyError: bodyErr}})
		if err != nil {
			return restError(c, err)
		}
	}
	if r.Method == http.MethodOptions {
		return c.JSON(200, map[string]any{"name": "Payment Info", "description": "", "renders": []string{"application/json"}, "parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}})
	}
	if r.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(r, "method")})
	}
	identifier := c.Param("uid")
	if r.URL.RawPath != "" {
		identifier, err = url.PathUnescape(identifier)
		if err != nil {
			return restError(c, err)
		}
	}
	data, err := h.info.Read(r.Context(), identity, identifier, r.Header.Get("X-Group-Id"))
	if err != nil {
		var invalid *service.PaymentInfoUUIDError
		if errors.As(err, &invalid) {
			return c.JSON(400, []string{invalid.Detail})
		}
		if errors.Is(err, service.ErrPaymentInfoNotFound) {
			return c.JSON(404, map[string]string{"msg": "Pagamento non trovato"})
		}
		return restError(c, err)
	}
	return c.JSON(200, map[string]any{"data": data})
}
