package handler

import (
	"context"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type PaymentArchiveService interface {
	Archive(context.Context, domain.ResolvedIdentity, string, string, domain.LoginAuditContext) (string, error)
}

type PaymentArchiveHandler struct {
	archives   PaymentArchiveService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewPaymentArchiveHandler(archives PaymentArchiveService, actors *service.ActorAuthentication, identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *PaymentArchiveHandler {
	return &PaymentArchiveHandler{archives: archives, actors: actors, identities: identities, audit: audit, operations: operations}
}

func (h *PaymentArchiveHandler) Archive(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "payment_archive")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, "Payment Archive")
	}
	if request.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
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
	message, err := h.archives.Archive(ctx, *identity, c.Param("uid"), request.Header.Get("X-Group-Id"), audit)
	switch {
	case errors.Is(err, service.ErrPaymentNotAllowed):
		return c.JSON(403, map[string]string{"msg": "User not allowed."})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, map[string]string{"msg": message})
}
