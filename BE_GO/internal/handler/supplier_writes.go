package handler

import (
	"context"
	"errors"
	"mime"
	"net/http"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type SupplierDeleteService interface {
	Delete(ctx context.Context, identity domain.ResolvedIdentity, raw, group string, audit domain.LoginAuditContext) error
	BulkDelete(ctx context.Context, identity domain.ResolvedIdentity, body any, group string, audit domain.LoginAuditContext) (string, error)
}

type SupplierWritesHandler struct {
	writes     SupplierWriteService
	deletes    SupplierDeleteService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewSupplierWritesHandler(writes SupplierWriteService, deletes SupplierDeleteService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *SupplierWritesHandler {
	return &SupplierWritesHandler{writes: writes, deletes: deletes, actors: actors, identities: identities, audit: audit, operations: operations}
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

type SupplierWriteService interface {
	Add(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool, group string, audit domain.LoginAuditContext) (*domain.Supplier, error)
	Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, form bool, group string, audit domain.LoginAuditContext) (*domain.Supplier, error)
}

// supplierErrorsRepr is f"validation errors: {serializer.errors}": the repr of
// the ReturnDict of ErrorDetail lists.
func supplierErrorsRepr(request *http.Request, invalid *dto.SupplierValidationError) string {
	english := restLanguage(request) == "en"
	var out strings.Builder
	out.WriteString("validation errors: {")
	for index, field := range invalid.Fields {
		if index > 0 {
			out.WriteString(", ")
		}
		out.WriteString(dto.PythonStringRepr(field.Name) + ": [")
		for position, code := range field.Codes {
			if position > 0 {
				out.WriteString(", ")
			}
			out.WriteString("ErrorDetail(string=" + dto.PythonStringRepr(paymentCategoryValidationMessage(code, english)) +
				", code=" + dto.PythonStringRepr(dto.SupplierDRFCode(code)) + ")")
		}
		out.WriteString("]")
	}
	out.WriteString("}")
	return out.String()
}

func (h *SupplierWritesHandler) write(c echo.Context, method, operation, name, description string,
	run func(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool, group string, audit domain.LoginAuditContext) (*domain.Supplier, error),
	status int) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, method, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, name, description)
	}
	if request.Method != method {
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
	media, _, _ := mime.ParseMediaType(request.Header.Get("Content-Type"))
	audit, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	row, err := run(ctx, *identity, body, media == "application/x-www-form-urlencoded" || media == "multipart/form-data",
		request.Header.Get("X-Group-Id"), audit)
	var invalid *dto.SupplierValidationError
	switch {
	case errors.Is(err, service.ErrSupplierForbidden):
		return c.NoContent(403)
	case errors.As(err, &invalid):
		return c.JSON(400, map[string]string{"msg": supplierErrorsRepr(request, invalid)})
	case errors.Is(err, service.ErrSupplierUpdateInvalid):
		return c.JSON(400, map[string]string{"msg": "validation errors."})
	case errors.Is(err, domain.ErrSupplierForeignReference):
		return restError(c, &domain.PermissionDenied{Detail: messageReadsPermissionDetail(request)})
	case errors.Is(err, domain.ErrSupplierDeleteDenied):
		return restError(c, &domain.PermissionDenied{Detail: "User not allowed."})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(status, dto.SupplierItemFrom(*row))
}

func (h *SupplierWritesHandler) Add(c echo.Context) error {
	return h.write(c, http.MethodPost, "supplier_add", "Supplier Add", "API endpoint to add a new supplier", h.writes.Add, 201)
}

func (h *SupplierWritesHandler) Update(c echo.Context) error {
	uid := c.Param("uid")
	return h.write(c, http.MethodPatch, "supplier_update", "Supplier Update", "API endpoint to update a new supplier",
		func(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool, group string, audit domain.LoginAuditContext) (*domain.Supplier, error) {
			return h.writes.Update(ctx, identity, uid, body, form, group, audit)
		}, 200)
}
