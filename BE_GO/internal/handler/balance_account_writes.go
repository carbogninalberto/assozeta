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

type BalanceAccountWriteService interface {
	Add(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool, group string, audit domain.LoginAuditContext) (dto.BalanceAccountItem, error)
	Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, form bool, group string, audit domain.LoginAuditContext) (dto.BalanceAccountItem, error)
}

type BalanceAccountWritesHandler struct {
	writes     BalanceAccountWriteService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewBalanceAccountWritesHandler(writes BalanceAccountWriteService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *BalanceAccountWritesHandler {
	return &BalanceAccountWritesHandler{writes: writes, actors: actors, identities: identities, audit: audit, operations: operations}
}

func (h *BalanceAccountWritesHandler) serve(c echo.Context, method, operation, name string,
	run func(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool, group string, audit domain.LoginAuditContext) (dto.BalanceAccountItem, error)) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, method, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, name, "")
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
	item, err := run(ctx, *identity, body, media == "application/x-www-form-urlencoded" || media == "multipart/form-data",
		request.Header.Get("X-Group-Id"), audit)
	var invalid *service.BalanceAccountValidationError
	switch {
	case errors.As(err, &invalid):
		return c.JSON(400, paymentCategoryValidationMessages(request, invalid.Fields))
	case errors.Is(err, service.ErrBalanceInfoUnavailable):
		return c.JSON(403, map[string]string{"msg": "Info not available."})
	case errors.Is(err, service.ErrBalanceAccountMissing):
		return c.JSON(404, map[string]string{"exception": "CustomAccounts matching query does not exist."})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, item)
}

func (h *BalanceAccountWritesHandler) Add(c echo.Context) error {
	return h.serve(c, http.MethodPost, "balance_sheet_accounts_add", "Balance Sheet Accounts Add", h.writes.Add)
}

func (h *BalanceAccountWritesHandler) Update(c echo.Context) error {
	uid := c.Param("uid")
	return h.serve(c, http.MethodPatch, "balance_sheet_accounts_update", "Balance Sheet Accounts Update",
		func(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool, group string, audit domain.LoginAuditContext) (dto.BalanceAccountItem, error) {
			return h.writes.Update(ctx, identity, uid, body, form, group, audit)
		})
}
