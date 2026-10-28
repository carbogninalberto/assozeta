package handler

import (
	"context"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type BalanceAccountService interface {
	Accounts(context.Context, domain.ResolvedIdentity, string, string) (map[string]any, error)
	Transfers(context.Context, domain.ResolvedIdentity) (map[string]any, error)
}

type BalanceSheetService interface {
	Archived(context.Context, domain.ResolvedIdentity) (map[string]any, error)
}

type BalanceAccountsHandler struct {
	accounts   BalanceAccountService
	sheets     BalanceSheetService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewBalanceAccountsHandler(accounts BalanceAccountService, sheets BalanceSheetService, actors *service.ActorAuthentication, identities *service.IdentityResolver) *BalanceAccountsHandler {
	return &BalanceAccountsHandler{accounts: accounts, sheets: sheets, actors: actors, identities: identities}
}

func (h *BalanceAccountsHandler) serve(c echo.Context, operation, name string, read func(*http.Request, domain.ResolvedIdentity) (map[string]any, error)) error {
	identity, err := messageReadsIdentity(c, h.actors, h.identities, http.MethodGet, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, name)
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	body, err := read(request, *identity)
	switch {
	case errors.Is(err, service.ErrBalanceInfoUnavailable):
		return c.JSON(403, map[string]string{"msg": "Info not available."})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, body)
}

func (h *BalanceAccountsHandler) Accounts(c echo.Context) error {
	return h.serve(c, "balance_sheet_accounts_list", "Balance Sheet Accounts List", func(request *http.Request, identity domain.ResolvedIdentity) (map[string]any, error) {
		related := "true"
		if value, ok := parseReferenceForm(request.URL.RawQuery).Lookup("related"); ok {
			related, _ = value.(string)
		}
		return h.accounts.Accounts(request.Context(), identity, related, request.Header.Get("X-Group-Id"))
	})
}

func (h *BalanceAccountsHandler) Transfers(c echo.Context) error {
	return h.serve(c, "balance_sheet_accounts_transfer_list", "Balance Sheet Accounts Transfer List", func(request *http.Request, identity domain.ResolvedIdentity) (map[string]any, error) {
		return h.accounts.Transfers(request.Context(), identity)
	})
}

func (h *BalanceAccountsHandler) Archived(c echo.Context) error {
	return h.serve(c, "balance_sheet_archived", "Balance Sheet Archived", func(request *http.Request, identity domain.ResolvedIdentity) (map[string]any, error) {
		return h.sheets.Archived(request.Context(), identity)
	})
}
