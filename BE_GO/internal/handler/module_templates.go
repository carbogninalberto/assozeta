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

type ModuleTemplateService interface {
	List(context.Context, domain.ResolvedIdentity) ([]dto.ModuleTemplateRow, error)
	Add(context.Context, domain.ResolvedIdentity, any, bool, domain.LoginAuditContext) (dto.ModuleTemplateRow, error)
	Delete(context.Context, domain.ResolvedIdentity, string, domain.LoginAuditContext) error
	BulkDelete(context.Context, domain.ResolvedIdentity, any, domain.LoginAuditContext) error
}

type ModuleTemplatesHandler struct {
	templates  ModuleTemplateService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewModuleTemplatesHandler(templates ModuleTemplateService, actors *service.ActorAuthentication, identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *ModuleTemplatesHandler {
	return &ModuleTemplatesHandler{templates: templates, actors: actors, identities: identities, audit: audit, operations: operations}
}

const moduleTemplatesView = "Sport Association Module Templates"

// The viewset defines a method named delete(), so DRF allows and dispatches
// DELETE on every route; list also maps HEAD to its get action.
func (h *ModuleTemplatesHandler) List(c echo.Context) error {
	return h.serve(c, "list", http.MethodGet, "GET, DELETE, HEAD, OPTIONS")
}
func (h *ModuleTemplatesHandler) Add(c echo.Context) error {
	return h.serve(c, "add", http.MethodPost, "POST, DELETE, OPTIONS")
}
func (h *ModuleTemplatesHandler) Delete(c echo.Context) error {
	return h.serve(c, "delete", http.MethodDelete, "DELETE, OPTIONS")
}
func (h *ModuleTemplatesHandler) BulkDelete(c echo.Context) error {
	return h.serve(c, "bulk_delete", http.MethodDelete, "DELETE, OPTIONS")
}

func (h *ModuleTemplatesHandler) serve(c echo.Context, action, method, allow string) error {
	c.Response().Header().Set("Allow", allow)
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, method, "module_templates_"+action)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		if action == "add" {
			return moduleTemplatesAddOptions(c)
		}
		return messageReadsOptions(c, moduleTemplatesView)
	}
	allowed := false
	for _, name := range strings.Split(allow, ", ") {
		allowed = allowed || name == request.Method
	}
	if !allowed {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	if request.Method == http.MethodDelete && method != http.MethodDelete {
		// delete() without a pk: get_object raises an AssertionError.
		return messageReadsError(c, errors.New("module template delete without pk"))
	}
	if action == "list" {
		rows, err := h.templates.List(request.Context(), *identity)
		if err != nil {
			return messageReadsError(c, err)
		}
		return c.JSON(200, rows)
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	var body any
	if action != "delete" {
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
	switch action {
	case "add":
		media, _, _ := mime.ParseMediaType(request.Header.Get("Content-Type"))
		form := media == "application/x-www-form-urlencoded" || media == "multipart/form-data"
		row, err := h.templates.Add(ctx, *identity, body, form, audit)
		var invalid *service.ModuleTemplateValidation
		if errors.As(err, &invalid) {
			return c.JSON(400, paymentCategoryValidationMessages(request, invalid.Errors))
		}
		if err != nil {
			return messageReadsError(c, err)
		}
		return c.JSON(201, row)
	case "delete":
		err = h.templates.Delete(ctx, *identity, c.Param("pk"), audit)
	default:
		err = h.templates.BulkDelete(ctx, *identity, body, audit)
		if errors.Is(err, dto.ErrModuleTemplatesNoInstances) {
			return c.JSON(400, map[string]string{"exception": err.Error()})
		}
	}
	switch {
	case errors.Is(err, service.ErrModuleTemplateLookup):
		return restError(c, &restInputError{Status: 404, Detail: restText(request, "notfound")})
	case errors.Is(err, domain.ErrModuleTemplateNotFound):
		// Django's get_object_or_404 message is not translated.
		return restError(c, &restInputError{Status: 404, Detail: err.Error()})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.NoContent(204)
}

// moduleTemplatesAddOptions is SimpleMetadata for the create action,
// including the serializer's POST field descriptions.
func moduleTemplatesAddOptions(c echo.Context) error {
	text := func(label string) map[string]any {
		return map[string]any{"type": "string", "required": false, "read_only": false, "label": label}
	}
	flag := func(label string) map[string]any {
		return map[string]any{"type": "boolean", "required": false, "read_only": false, "label": label}
	}
	return c.JSON(200, map[string]any{
		"name": moduleTemplatesView, "description": "",
		"renders": []string{"application/json"},
		"parses":  []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"},
		"actions": map[string]any{"POST": map[string]any{
			"sport_association_module_templates_id": text("Sport association module templates id"),
			"name":                                  map[string]any{"type": "string", "required": true, "read_only": false, "label": "Name", "max_length": 150},
			"template":                              text("Template"),
			"footer":                                flag("Footer"),
			"header":                                flag("Header"),
			"custom_footer_header":                  flag("Custom footer header"),
			"custom_footer":                         text("Custom footer"),
			"custom_header":                         text("Custom header"),
			"sport_association":                     map[string]any{"type": "field", "required": true, "read_only": false, "label": "Sport association"},
		}},
	})
}
