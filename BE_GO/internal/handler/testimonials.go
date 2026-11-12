package handler

import (
	"context"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type TestimonialService interface {
	Add(context.Context, domain.ResolvedIdentity, any, domain.LoginAuditContext) error
	Webhook(ctx context.Context, identity *domain.ResolvedIdentity, body any, signature, secret *string, audit domain.LoginAuditContext) error
}

type TestimonialsHandler struct {
	testimonials TestimonialService
	actors       *service.ActorAuthentication
	identities   *service.IdentityResolver
	audit        LoginAuditReader
	operations   *paymentcategorylifecycle.Lifecycle
	secret       *string
}

func NewTestimonialsHandler(testimonials TestimonialService, actors *service.ActorAuthentication, identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle, webhookSecret *string) *TestimonialsHandler {
	return &TestimonialsHandler{testimonials: testimonials, actors: actors, identities: identities, audit: audit, operations: operations, secret: webhookSecret}
}

// Add is IsAuthenticated only: OPTIONS never evaluates sport_association.
func (h *TestimonialsHandler) Add(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "testimonials_create")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, "Testimonials Create")
	}
	if request.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	body, err := readMessageMutationsBody(c)
	if err != nil {
		return restError(c, err)
	}
	audit, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	operationContext, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	err = h.testimonials.Add(operationContext, *identity, body, audit)
	switch {
	case errors.Is(err, dto.ErrTestimonialMissing), errors.Is(err, dto.ErrTestimonialScore):
		return c.JSON(400, map[string]string{"msg": err.Error()})
	case errors.Is(err, domain.ErrEmailLogsNotAssociation):
		return restError(c, &domain.PermissionDenied{Detail: err.Error()})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, map[string]string{"msg": "Thank you for your review."})
}

// Webhook is the Senja endpoint: AllowAny with default authentication, so a
// supplied token still passes identity resolution and the registry.
func (h *TestimonialsHandler) Webhook(c echo.Context) error {
	identity, err := messageMutationsIdentityFor(c, h.actors, h.identities, http.MethodPost, "testimonials_update", true)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, "Testimonials Update")
	}
	if request.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	body, err := readMessageMutationsBody(c)
	if err != nil {
		return restError(c, err)
	}
	audit, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	operationContext, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	var signature *string
	if values, present := request.Header[http.CanonicalHeaderKey("X-Senja-Signature")]; present && len(values) > 0 {
		signature = &values[0]
	}
	var resolved *domain.ResolvedIdentity
	if identity.ScopeUser != nil {
		resolved = identity
	}
	err = h.testimonials.Webhook(operationContext, resolved, body, signature, h.secret, audit)
	switch {
	case errors.Is(err, service.ErrTestimonialWebhookUnsigned):
		return c.NoContent(401)
	case errors.Is(err, service.ErrTestimonialWebhookInvalid):
		return c.NoContent(400)
	case errors.Is(err, service.ErrTestimonialWebhookUnknown):
		return c.NoContent(404)
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.NoContent(200)
}
