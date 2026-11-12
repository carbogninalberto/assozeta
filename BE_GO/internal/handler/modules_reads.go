package handler

import (
	"errors"
	"net/http"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// ModulesReadsHandler serves modules_list, modules_overview and
// modules_check_link. modules/<custom_link>/info (AllowAny) is not ported.
type ModulesReadsHandler struct {
	modules    *service.ModulesReads
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewModulesReadsHandler(modules *service.ModulesReads, actors *service.ActorAuthentication, identities *service.IdentityResolver) *ModulesReadsHandler {
	return &ModulesReadsHandler{modules: modules, actors: actors, identities: identities}
}

// admit runs @api_view(['GET']) + IsAuthenticated, OPTIONS and the 405.
func (h *ModulesReadsHandler) admit(c echo.Context, operation, name string) (*domain.ResolvedIdentity, bool, error) {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, operation)
	if identity == nil {
		return nil, false, err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return nil, false, supplierOptions(c, name, "")
	}
	if request.Method != http.MethodGet {
		return nil, false, restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	return identity, true, nil
}

func (h *ModulesReadsHandler) answer(c echo.Context, body map[string]any, err error) error {
	switch {
	case errors.Is(err, domain.ErrModuleNotFound):
		return c.JSON(404, map[string]string{"error": "module not found"})
	case err != nil:
		// PermissionDenied is the DRF 403; anything else is the 500 page.
		return restError(c, err)
	}
	return c.JSON(200, body)
}

// List mirrors modules_list.
func (h *ModulesReadsHandler) List(c echo.Context) error {
	identity, ok, err := h.admit(c, "modules_list", "Modules List")
	if !ok {
		return err
	}
	body, err := h.modules.List(c.Request().Context(), *identity)
	return h.answer(c, body, err)
}

// Overview mirrors modules_overview.
func (h *ModulesReadsHandler) Overview(c echo.Context) error {
	identity, ok, err := h.admit(c, "modules_overview", "Modules Overview")
	if !ok {
		return err
	}
	body, err := h.modules.Overview(c.Request().Context(), *identity, c.Param("module_id"))
	return h.answer(c, body, err)
}

// CheckLink mirrors modules_check_link: query_params (QueryDict, last value
// of a repeated key) and the 'module' request header.
func (h *ModulesReadsHandler) CheckLink(c echo.Context) error {
	_, ok, err := h.admit(c, "modules_check_link", "Modules Check Link")
	if !ok {
		return err
	}
	request := c.Request()
	var link, header *string
	if value, present := parseImpersonationQuery(request.URL.RawQuery).Lookup("custom_link"); present {
		text, _ := value.(string)
		link = &text
	}
	if values := request.Header.Values("Module"); len(values) > 0 {
		joined := strings.Join(values, ",")
		header = &joined
	}
	err = h.modules.CheckLink(request.Context(), link, header)
	if errors.Is(err, service.ErrModuleLinkInvalid) {
		return c.JSON(400, map[string]any{"valid": false, "exception": "invalid payload."})
	}
	if err != nil {
		return restError(c, err)
	}
	return c.JSON(200, map[string]bool{"valid": true})
}
