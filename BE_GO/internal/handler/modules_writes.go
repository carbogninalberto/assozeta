package handler

import (
	"context"
	"errors"
	"net/http"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type ModulesWriteService interface {
	Add(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool, audit domain.LoginAuditContext) error
	Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, form bool, audit domain.LoginAuditContext) (map[string]any, error)
	Duplicate(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) (map[string]any, error)
	Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error
}

// ModulesWritesHandler serves modules_add, modules_update, modules_duplicate
// and modules_delete (@api_view, IsAuthenticated).
type ModulesWritesHandler struct {
	writes     ModulesWriteService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewModulesWritesHandler(writes ModulesWriteService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *ModulesWritesHandler {
	return &ModulesWritesHandler{writes: writes, actors: actors, identities: identities, audit: audit, operations: operations}
}

// serve runs identity and the registry, OPTIONS, the 405, the write
// prologue and the view; update renders validation errors as str(e).
func (h *ModulesWritesHandler) serve(c echo.Context, method, operation, name string, update bool,
	run func(write *restWrite, identity domain.ResolvedIdentity) (int, any, error)) error {
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
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	status, result, err := run(write, *identity)
	var invalidUUID *service.PaymentInfoUUIDError
	var invalid *service.ModuleValidationError
	var failure *domain.ModuleUpdateFailure
	english := restLanguage(request) == "en"
	switch {
	case err == nil:
		return c.JSON(status, result)
	case errors.As(err, &invalidUUID):
		return c.JSON(400, []string{invalidUUID.Detail})
	case errors.As(err, &invalid) && update:
		return c.JSON(400, map[string]string{"error": moduleErrorsRepr(invalid.Errors, english)})
	case errors.As(err, &invalid):
		fields := make(map[string]any, len(invalid.Errors))
		for field, codes := range invalid.Errors {
			messages := make([]string, len(codes))
			for index, code := range codes {
				messages[index] = moduleMessage(code, english)
			}
			fields[field] = messages
		}
		return c.JSON(400, fields)
	case errors.As(err, &failure):
		return c.JSON(failure.Status, map[string]string{"error": failure.Text})
	case errors.Is(err, domain.ErrModuleNotFound):
		return c.JSON(404, map[string]string{"error": "module not found"})
	case errors.Is(err, domain.ErrModuleUpdateNotFound):
		return c.JSON(404, map[string]string{"error": "Camp or retreat not found"})
	}
	// PermissionDenied is the DRF 403; anything else (including the inputs
	// that are not ported) is the 500 page.
	return restError(c, err)
}

func moduleMessage(code string, english bool) string {
	switch {
	case code == dto.ModuleDate:
		if english {
			return "Date has wrong format. Use one of these formats instead: DD/MM/YYYY."
		}
		return "La data è in un formato errato. Usa uno dei seguenti formati: DD/MM/YYYY."
	case code == dto.ModuleJSON:
		if english {
			return "Value must be valid JSON."
		}
		return "Il valore deve essere un JSON valido."
	case strings.HasPrefix(code, dto.InvoicePartyMaxValue):
		limit := strings.TrimPrefix(code, dto.InvoicePartyMaxValue)
		if english {
			return "Ensure this value is less than or equal to " + limit + "."
		}
		return "Assicurati che il valore sia minore o uguale a " + limit + "."
	}
	return paymentCategoryValidationMessage(code, english)
}

// moduleErrorsRepr is str(ValidationError(serializer.errors)): the dict repr
// of ErrorDetail lists in field order; the nested serializer's non-dict error
// is its own {'non_field_errors': [...]} dict.
func moduleErrorsRepr(errors dto.ModuleWriteErrors, english bool) string {
	detail := func(code string) string {
		return "[ErrorDetail(string=" + dto.PythonStringRepr(moduleMessage(code, english)) + ", code=" +
			dto.PythonStringRepr(dto.ModuleDRFCode(code)) + ")]"
	}
	order := append([]string{"non_field_errors"}, dto.ModuleFieldOrder...)
	parts := []string{}
	for _, field := range order {
		codes, ok := errors[field]
		if !ok {
			continue
		}
		text := ""
		if len(codes) == 1 && strings.HasPrefix(codes[0], dto.ModuleNestedDictionary) {
			text = "{'non_field_errors': " + detail("dictionary:"+strings.TrimPrefix(codes[0], dto.ModuleNestedDictionary)) + "}"
		} else {
			items := make([]string, len(codes))
			for index, code := range codes {
				items[index] = strings.TrimSuffix(strings.TrimPrefix(detail(code), "["), "]")
			}
			text = "[" + strings.Join(items, ", ") + "]"
		}
		parts = append(parts, dto.PythonStringRepr(field)+": "+text)
	}
	return "{" + strings.Join(parts, ", ") + "}"
}

func (h *ModulesWritesHandler) Add(c echo.Context) error {
	return h.serve(c, http.MethodPost, "modules_add", "Modules Add", false, func(write *restWrite, identity domain.ResolvedIdentity) (int, any, error) {
		err := h.writes.Add(write.Context, identity, write.Body, write.Form, write.Audit)
		return 200, map[string]string{"msg": "module created"}, err
	})
}

func (h *ModulesWritesHandler) Update(c echo.Context) error {
	return h.serve(c, http.MethodPatch, "modules_update", "Modules Update", true, func(write *restWrite, identity domain.ResolvedIdentity) (int, any, error) {
		body, err := h.writes.Update(write.Context, identity, c.Param("module_id"), write.Body, write.Form, write.Audit)
		return 200, body, err
	})
}

func (h *ModulesWritesHandler) Duplicate(c echo.Context) error {
	return h.serve(c, http.MethodPost, "modules_duplicate", "Modules Duplicate", false, func(write *restWrite, identity domain.ResolvedIdentity) (int, any, error) {
		body, err := h.writes.Duplicate(write.Context, identity, c.Param("module_id"), write.Audit)
		return 201, body, err
	})
}

func (h *ModulesWritesHandler) Delete(c echo.Context) error {
	return h.serve(c, http.MethodDelete, "modules_delete", "Modules Delete", false, func(write *restWrite, identity domain.ResolvedIdentity) (int, any, error) {
		err := h.writes.Delete(write.Context, identity, c.Param("module_id"), write.Audit)
		return 200, map[string]string{"msg": "module deleted"}, err
	})
}
