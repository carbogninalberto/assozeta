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

type CollaboratorUpdateService interface {
	Update(context.Context, domain.ResolvedIdentity, string, any, domain.LoginAuditContext) error
}

// CollaboratorWritesHandler serves collaborators/<uid>/update
// (@api_view(['PATCH'])). collaborators/add is not routed: every success
// path dispatches the invitation e-mail through Celery.
type CollaboratorWritesHandler struct {
	updates    CollaboratorUpdateService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewCollaboratorWritesHandler(updates CollaboratorUpdateService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *CollaboratorWritesHandler {
	return &CollaboratorWritesHandler{updates: updates, actors: actors, identities: identities, audit: audit, operations: operations}
}

func (h *CollaboratorWritesHandler) Update(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPatch, "collaborators_update")
	if identity == nil {
		return err
	}
	request := c.Request()
	// IsAssociationUser runs with the other permissions, before method dispatch.
	if role := identity.ScopeUser.Role; role != domain.RoleAssociation && role != domain.RoleCollaborator {
		return restError(c, &domain.PermissionDenied{Detail: messageReadsPermissionDetail(request)})
	}
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, "Collaborators Update")
	}
	if request.Method != http.MethodPatch {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	// request.data is parsed before is_valid_uuid.
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	err = h.updates.Update(write.Context, *identity, c.Param("uid"), write.Body, write.Audit)
	var malformed *service.PaymentInfoUUIDError
	switch {
	case errors.As(err, &malformed):
		return c.JSON(400, []string{malformed.Detail})
	case errors.Is(err, domain.ErrCollaboratorNotFound):
		return c.JSON(400, []string{err.Error()})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, map[string]string{"msg": "collaborator updated."})
}
