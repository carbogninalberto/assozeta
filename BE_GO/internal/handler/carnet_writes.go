package handler

import (
	"context"
	"errors"
	"mime"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type CarnetDeleteService interface {
	Delete(context.Context, domain.ResolvedIdentity, string, domain.LoginAuditContext) error
}

type CarnetUpdateService interface {
	Update(context.Context, domain.ResolvedIdentity, string, any, bool, domain.LoginAuditContext) error
}

type CarnetWritesHandler struct {
	deletes    CarnetDeleteService
	updates    CarnetUpdateService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewCarnetWritesHandler(deletes CarnetDeleteService, updates CarnetUpdateService, actors *service.ActorAuthentication, identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *CarnetWritesHandler {
	return &CarnetWritesHandler{deletes: deletes, updates: updates, actors: actors, identities: identities, audit: audit, operations: operations}
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

func (h *CarnetWritesHandler) Update(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPatch, "carnet_update")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Carnet Update", "Update a carnet")
	}
	if request.Method != http.MethodPatch {
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
	err = h.updates.Update(ctx, *identity, c.Param("uid"), body, media == "application/x-www-form-urlencoded" || media == "multipart/form-data", audit)
	var invalid *dto.CarnetUpdateValidationError
	switch {
	case errors.As(err, &invalid):
		fields := make(map[string]any, len(invalid.Fields))
		for name, codes := range invalid.Fields {
			fields[name] = codes
		}
		return c.JSON(400, map[string]any{"msg": paymentCategoryValidationMessages(request, fields)})
	case errors.Is(err, service.ErrCarnetNotAllowed):
		return c.JSON(403, map[string]string{"msg": "Not allowed."})
	case errors.Is(err, domain.ErrCarnetNotFound):
		return c.JSON(404, map[string]string{"msg": "Carnet not found."})
	case err != nil:
		return restError(c, err)
	}
	return c.JSON(200, map[string]string{"msg": "Carnet updated successfully."})
}
