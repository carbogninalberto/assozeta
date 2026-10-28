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

type CarnetDeleteService interface {
	Delete(context.Context, domain.ResolvedIdentity, string, domain.LoginAuditContext) error
}

type CarnetWritesHandler struct {
	deletes    CarnetDeleteService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewCarnetWritesHandler(deletes CarnetDeleteService, actors *service.ActorAuthentication, identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *CarnetWritesHandler {
	return &CarnetWritesHandler{deletes: deletes, actors: actors, identities: identities, audit: audit, operations: operations}
}

func (h *CarnetWritesHandler) Delete(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "carnet_delete")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Carnet Delete", "Delete a carnet")
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
	err = h.deletes.Delete(ctx, *identity, c.Param("uid"), audit)
	switch {
	case errors.Is(err, service.ErrCarnetNotAllowed):
		return c.JSON(403, map[string]string{"msg": "Not allowed."})
	case errors.Is(err, domain.ErrCarnetNotFound):
		return c.JSON(404, map[string]string{"msg": "Carnet not found."})
	case err != nil:
		return restError(c, err)
	}
	return c.JSON(200, map[string]string{"msg": "Carnet deleted successfully."})
}
