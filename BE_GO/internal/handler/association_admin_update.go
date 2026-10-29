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

type AssociationAdminService interface {
	Update(context.Context, domain.ResolvedIdentity, string, any, domain.LoginAuditContext) error
}

type AssociationAdminHandler struct {
	updates    AssociationAdminService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewAssociationAdminHandler(updates AssociationAdminService, actors *service.ActorAuthentication, identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *AssociationAdminHandler {
	return &AssociationAdminHandler{updates: updates, actors: actors, identities: identities, audit: audit, operations: operations}
}

func (h *AssociationAdminHandler) Update(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "sport_association_admin_update")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		// The view reuses the list docstring.
		return c.JSON(200, map[string]any{"name": "Sport Association Admin Update", "description": "List all sport associations",
			"renders": []string{"application/json"},
			"parses":  []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}})
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
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	err = h.updates.Update(ctx, *identity, c.Param("uid"), body, audit)
	switch {
	case errors.Is(err, service.ErrAdminUpdateNotSuperuser):
		return c.NoContent(401)
	case errors.Is(err, domain.ErrAdminUpdateNotFound):
		return c.NoContent(404)
	case errors.Is(err, service.ErrAdminUpdateMissingBilling):
		return c.NoContent(400)
	case errors.Is(err, domain.ErrAdminUpdateNotes):
		return c.JSON(400, map[string]string{"msg": err.Error()})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, map[string]string{"msg": "Modifiche salvate."})
}
