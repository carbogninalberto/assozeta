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

type SupplierDeleteService interface {
	Delete(ctx context.Context, identity domain.ResolvedIdentity, raw, group string, audit domain.LoginAuditContext) error
	BulkDelete(ctx context.Context, identity domain.ResolvedIdentity, body any, group string, audit domain.LoginAuditContext) (string, error)
}

type SupplierWritesHandler struct {
	deletes    SupplierDeleteService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewSupplierWritesHandler(deletes SupplierDeleteService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *SupplierWritesHandler {
	return &SupplierWritesHandler{deletes: deletes, actors: actors, identities: identities, audit: audit, operations: operations}
}

// begin is the shared DELETE preamble: identity, OPTIONS, method, admission
// and audit context.
func (h *SupplierWritesHandler) begin(c echo.Context, operation, name, description string) (*domain.ResolvedIdentity, context.Context,
	domain.LoginAuditContext, func(), error, bool) {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodDelete, operation)
	if identity == nil {
		return nil, nil, domain.LoginAuditContext{}, nil, err, true
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return nil, nil, domain.LoginAuditContext{}, nil, supplierOptions(c, name, description), true
	}
	if request.Method != http.MethodDelete {
		return nil, nil, domain.LoginAuditContext{}, nil, restError(c, &restInputError{Status: 405, Detail: restText(request, "method")}), true
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return nil, nil, domain.LoginAuditContext{}, nil, paymentCategoryError(c, err), true
	}
	audit, err := h.audit(request)
	if err != nil {
		tracked.Release()
		return nil, nil, domain.LoginAuditContext{}, nil, restError(c, err), true
	}
	ctx, err := tracked.Admit()
	if err != nil {
		tracked.Release()
		return nil, nil, domain.LoginAuditContext{}, nil, paymentCategoryError(c, err), true
	}
	return identity, ctx, audit, tracked.Release, nil, false
}

func (h *SupplierWritesHandler) Delete(c echo.Context) error {
	identity, ctx, audit, release, err, done := h.begin(c, "supplier_delete", "Supplier Delete", "API endpoint to delete a supplier")
	if done {
		return err
	}
	defer release()
	err = h.deletes.Delete(ctx, *identity, c.Param("uid"), c.Request().Header.Get("X-Group-Id"), audit)
	switch {
	case errors.Is(err, domain.ErrSupplierDeleteDenied):
		return restError(c, &domain.PermissionDenied{Detail: "User not allowed."})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, map[string]any{"data": map[string]string{"msg": "supplier deleted."}})
}

func (h *SupplierWritesHandler) BulkDelete(c echo.Context) error {
	identity, ctx, audit, release, err, done := h.begin(c, "supplier_bulk_delete", "Supplier Bulk Delete",
		"API endpoint to delete multiple suppliers at once")
	if done {
		return err
	}
	defer release()
	body, err := readMessageMutationsBody(c)
	if err != nil {
		return restError(c, err)
	}
	message, err := h.deletes.BulkDelete(ctx, *identity, body, c.Request().Header.Get("X-Group-Id"), audit)
	switch {
	case errors.Is(err, service.ErrSupplierForbidden):
		return c.NoContent(403)
	case errors.Is(err, service.ErrSupplierBulkMissing), errors.Is(err, service.ErrSupplierBulkInvalid):
		return c.JSON(400, map[string]string{"msg": err.Error()})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, map[string]any{"data": map[string]string{"msg": message}})
}
