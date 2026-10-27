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
}

type TestimonialsHandler struct {
	testimonials TestimonialService
	actors       *service.ActorAuthentication
	identities   *service.IdentityResolver
	audit        LoginAuditReader
	operations   *paymentcategorylifecycle.Lifecycle
}

func NewTestimonialsHandler(testimonials TestimonialService, actors *service.ActorAuthentication, identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *TestimonialsHandler {
	return &TestimonialsHandler{testimonials: testimonials, actors: actors, identities: identities, audit: audit, operations: operations}
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
