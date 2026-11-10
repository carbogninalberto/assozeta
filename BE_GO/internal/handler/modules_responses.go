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
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
	"github.com/labstack/echo/v4"
)

type ModulesResponsesService interface {
	Info(ctx context.Context, link string, responseID *string) (map[string]any, error)
	Add(ctx context.Context, identity domain.ResolvedIdentity, raw string, body service.ModuleBody, audit domain.LoginAuditContext) (string, error)
	Approve(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error
	Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error
	Attachment(ctx context.Context, identity domain.ResolvedIdentity, method, raw string, body service.ModuleBody, audit domain.LoginAuditContext) (service.AttachmentResult, error)
}

// ModulesResponsesHandler serves modules_custom_link_info,
// modules_response_add and modules_response_add_attachment (no
// permission_classes: AllowAny) and modules_response_approve and
// modules_response_delete and modules_response_export (IsAuthenticated).
type ModulesResponsesHandler struct {
	responses  ModulesResponsesService
	export     *service.ModulesExport
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewModulesResponsesHandler(responses ModulesResponsesService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *ModulesResponsesHandler {
	return &ModulesResponsesHandler{responses: responses, actors: actors, identities: identities, audit: audit, operations: operations}
}

// admit runs identity and the registry (anonymous requests pass AllowAny
// views), OPTIONS and the 405.
func (h *ModulesResponsesHandler) admit(c echo.Context, methods []string, operation, name string, anonymous bool) (*domain.ResolvedIdentity, bool, error) {
	allow := ""
	for _, method := range methods {
		allow += method + ", "
	}
	c.Response().Header().Set("Allow", allow+"OPTIONS")
	identity, err := messageMutationsIdentityFor(c, h.actors, h.identities, methods[0], operation, anonymous)
	if identity == nil {
		return nil, false, err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return nil, false, supplierOptions(c, name, "")
	}
	for _, method := range methods {
		if request.Method == method {
			return identity, true, nil
		}
	}
	return nil, false, restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
}

// write runs the shared write prologue without reading the body: the view
// reads request.data at its own point (ModuleBody).
func (h *ModulesResponsesHandler) write(c echo.Context, run func(ctx context.Context, body service.ModuleBody, audit domain.LoginAuditContext) error) error {
	request := c.Request()
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
	media, _, _ := mime.ParseMediaType(request.Header.Get("Content-Type"))
	body := func() (any, bool, error) {
		value, err := readMessageMutationsBody(c)
		return value, media == "application/x-www-form-urlencoded" || media == "multipart/form-data", err
	}
	return run(ctx, body, audit)
}

func (h *ModulesResponsesHandler) answer(c echo.Context, status int, result any, err error) error {
	var invalidUUID *service.PaymentInfoUUIDError
	var suspicious *documentkey.SuspiciousFileError
	switch {
	case err == nil:
		return c.JSON(status, result)
	case errors.As(err, &suspicious):
		// Storage.save's SuspiciousFileOperation: Django's 400 page.
		c.Response().Header().Del("Allow")
		return c.Blob(400, "text/html; charset=utf-8", djangoBadRequestPage)
	case errors.As(err, &invalidUUID):
		return c.JSON(400, []string{invalidUUID.Detail})
	}
	for _, known := range []error{domain.ErrModuleNotFound, domain.ErrModuleNotEnabled, domain.ErrModuleNotActive, domain.ErrModuleMaxResponses,
		domain.ErrModuleOnlyUsers, domain.ErrModuleResponseNotFound, domain.ErrModuleDocumentNotFound} {
		if errors.Is(err, known) {
			return c.JSON(404, map[string]string{"error": known.Error()})
		}
	}
	// PermissionDenied is the DRF 403, a body parse error DRF's 400; anything
	// else (including the inputs that are not ported) is the 500 page.
	return restError(c, err)
}

// Info mirrors modules_custom_link_info.
func (h *ModulesResponsesHandler) Info(c echo.Context) error {
	_, ok, err := h.admit(c, []string{http.MethodGet}, "modules_custom_link_info", "Modules Custom Link Info", true)
	if !ok {
		return err
	}
	var responseID *string
	if value, present := parseImpersonationQuery(c.Request().URL.RawQuery).Lookup("response_id"); present {
		text, _ := value.(string)
		responseID = &text
	}
	body, err := h.responses.Info(c.Request().Context(), c.Param("module_id"), responseID)
	return h.answer(c, 200, body, err)
}

// Add mirrors modules_response_add.
func (h *ModulesResponsesHandler) Add(c echo.Context) error {
	identity, ok, err := h.admit(c, []string{http.MethodPost}, "modules_response_add", "Modules Response Add", true)
	if !ok {
		return err
	}
	return h.write(c, func(ctx context.Context, body service.ModuleBody, audit domain.LoginAuditContext) error {
		id, err := h.responses.Add(ctx, *identity, c.Param("module_id"), body, audit)
		return h.answer(c, 200, map[string]string{"msg": "response added", "module_response_id": id}, err)
	})
}

// Approve mirrors modules_response_approve.
func (h *ModulesResponsesHandler) Approve(c echo.Context) error {
	identity, ok, err := h.admit(c, []string{http.MethodPost}, "modules_response_approve", "Modules Response Approve", false)
	if !ok {
		return err
	}
	return h.write(c, func(ctx context.Context, _ service.ModuleBody, audit domain.LoginAuditContext) error {
		err := h.responses.Approve(ctx, *identity, c.Param("module_response_id"), audit)
		return h.answer(c, 200, map[string]string{"msg": "response approved"}, err)
	})
}

// Delete mirrors modules_response_delete.
func (h *ModulesResponsesHandler) Delete(c echo.Context) error {
	identity, ok, err := h.admit(c, []string{http.MethodDelete}, "modules_response_delete", "Modules Response Delete", false)
	if !ok {
		return err
	}
	return h.write(c, func(ctx context.Context, _ service.ModuleBody, audit domain.LoginAuditContext) error {
		err := h.responses.Delete(ctx, *identity, c.Param("module_response_id"), audit)
		return h.answer(c, 200, map[string]string{"msg": "response deleted"}, err)
	})
}

// WithExport serves modules_response_export through the export service.
func (h *ModulesResponsesHandler) WithExport(export *service.ModulesExport) *ModulesResponsesHandler {
	h.export = export
	return h
}

// Export mirrors modules_response_export (a read: no write prologue).
func (h *ModulesResponsesHandler) Export(c echo.Context) error {
	identity, ok, err := h.admit(c, []string{http.MethodGet}, "modules_response_export", "Modules Response Export", false)
	if !ok {
		return err
	}
	body, err := h.export.Export(c.Request().Context(), *identity, c.Param("module_id"))
	if err == nil {
		return c.Blob(http.StatusOK, echo.MIMEApplicationJSON, dto.PythonJSONDumpsCompact(body))
	}
	return h.answer(c, 200, nil, err)
}

// Attachment mirrors modules_response_add_attachment (POST and DELETE).
func (h *ModulesResponsesHandler) Attachment(c echo.Context) error {
	identity, ok, err := h.admit(c, []string{http.MethodPost, http.MethodDelete}, "modules_response_add_attachment",
		"Modules Response Add Attachment", true)
	if !ok {
		return err
	}
	return h.write(c, func(ctx context.Context, body service.ModuleBody, audit domain.LoginAuditContext) error {
		result, err := h.responses.Attachment(ctx, *identity, c.Request().Method, c.Param("module_response_id"), body, audit)
		return h.answer(c, 200, map[string]string{"msg": string(result)}, err)
	})
}
