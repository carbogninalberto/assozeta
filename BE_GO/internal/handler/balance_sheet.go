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

type BalanceSheetWriteService interface {
	Sheet(ctx context.Context, identity domain.ResolvedIdentity, request service.BalanceSheetRequest) (any, error)
}

type BalanceAccountDeleteService interface {
	Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext, groupHeader string) error
}

type BalanceSheetHandler struct {
	sheets     BalanceSheetWriteService
	deletes    BalanceAccountDeleteService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewBalanceSheetHandler(sheets BalanceSheetWriteService, deletes BalanceAccountDeleteService, actors *service.ActorAuthentication,
	identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *BalanceSheetHandler {
	return &BalanceSheetHandler{sheets: sheets, deletes: deletes, actors: actors, identities: identities, audit: audit, operations: operations}
}

// serve runs one admitted request of the balance-sheet views.
func (h *BalanceSheetHandler) serve(c echo.Context, allowed map[string]bool, operation, name string,
	run func(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) (any, error)) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodDelete, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, name, "")
	}
	if !allowed[request.Method] {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	// DELETE views never read request.data.
	var body any
	if request.Method != http.MethodDelete {
		if body, err = readMessageMutationsBody(c); err != nil {
			return restError(c, err)
		}
	}
	audit, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	result, err := run(ctx, *identity, body, audit)
	switch {
	case errors.Is(err, service.ErrBalanceInfoUnavailable):
		return c.JSON(403, map[string]string{"msg": "Info not available."})
	case errors.Is(err, service.ErrBalanceSheetInvalid):
		return c.JSON(400, map[string]string{"msg": "Invalid data."})
	case errors.Is(err, service.ErrBalanceSheetMissing):
		return c.JSON(404, map[string]string{"msg": "No balance sheet found."})
	case errors.Is(err, service.ErrBalanceAccountMissing):
		return c.JSON(404, map[string]string{"exception": "CustomAccounts matching query does not exist."})
	case errors.Is(err, service.ErrBalanceAccountNotDeletable):
		return c.JSON(403, map[string]string{"msg": "This account is not deletable."})
	case errors.Is(err, service.ErrBalanceAccountInUse):
		return c.JSON(403, map[string]string{"msg": "An account with transfers or payments cannot be deleted."})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, result)
}

// Sheet is balance_sheet (GET, POST, DELETE).
func (h *BalanceSheetHandler) Sheet(c echo.Context) error {
	c.Response().Header().Set("Allow", "GET, POST, DELETE, OPTIONS")
	allowed := map[string]bool{http.MethodGet: true, http.MethodPost: true, http.MethodDelete: true}
	return h.serve(c, allowed, "balance_sheet", "Balance Sheet",
		func(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) (any, error) {
			request := c.Request()
			var current *string
			if value, ok := parseReferenceForm(request.URL.RawQuery).Lookup("currentDate"); ok {
				text, _ := value.(string)
				current = &text
			}
			return h.sheets.Sheet(ctx, identity, service.BalanceSheetRequest{Method: request.Method, CurrentDate: current, Body: body,
				Group: request.Header.Get("X-Group-Id"), Audit: audit})
		})
}

// AccountDelete is balance_sheet_accounts_delete.
func (h *BalanceSheetHandler) AccountDelete(c echo.Context) error {
	uid, group := c.Param("uid"), c.Request().Header.Get("X-Group-Id")
	return h.serve(c, map[string]bool{http.MethodDelete: true}, "balance_sheet_accounts_delete", "Balance Sheet Accounts Delete",
		func(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) (any, error) {
			if err := h.deletes.Delete(ctx, identity, uid, audit, group); err != nil {
				return nil, err
			}
			return map[string]string{"msg": "bank accounts deleted"}, nil
		})
}
