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

type CollaboratorDeleteService interface {
	Delete(ctx context.Context, identity domain.ResolvedIdentity, uid, groupHeader string, audit domain.LoginAuditContext) error
}

// CollaboratorDeleteHandler serves collaborators/<uid>/delete
// (@api_view(['DELETE']), IsAuthenticated). The request body is never read.
type CollaboratorDeleteHandler struct {
	deletes    CollaboratorDeleteService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewCollaboratorDeleteHandler(deletes CollaboratorDeleteService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *CollaboratorDeleteHandler {
	return &CollaboratorDeleteHandler{deletes: deletes, actors: actors, identities: identities, audit: audit, operations: operations}
}

func (h *CollaboratorDeleteHandler) Delete(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodDelete, "collaborators_delete")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, "Collaborators Delete")
	}
	if request.Method != http.MethodDelete {
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
	err = h.deletes.Delete(ctx, *identity, c.Param("uid"), request.Header.Get("X-Group-Id"), audit)
	var malformed *service.PaymentInfoUUIDError
	switch {
	case errors.Is(err, domain.ErrCollaboratorDeleteNotSportAssociation):
		return restError(c, &domain.PermissionDenied{Detail: err.Error()})
	case errors.As(err, &malformed):
		return c.JSON(400, []string{malformed.Detail})
	case errors.Is(err, domain.ErrCollaboratorNotFound):
		return c.JSON(400, []string{err.Error()})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, map[string]string{"msg": "collaborator deleted."})
}
