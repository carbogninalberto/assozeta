package handler

import (
	"context"
	"errors"
	"mime"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type CourseLocationWriteService interface {
	Add(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool, audit domain.LoginAuditContext) (map[string]any, error)
	Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, form bool, audit domain.LoginAuditContext) (map[string]any, error)
	Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error
}

type CourseLocationWritesHandler struct {
	writes     CourseLocationWriteService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewCourseLocationWritesHandler(writes CourseLocationWriteService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *CourseLocationWritesHandler {
	return &CourseLocationWritesHandler{writes: writes, actors: actors, identities: identities, audit: audit, operations: operations}
}

// courseLocationAddActions is SimpleMetadata's POST description of
// CourseLocationSerializer, the only PUT/POST method allowed on add.
var courseLocationAddActions = func() map[string]any {
	field := func(kind, label string, extra map[string]any) map[string]any {
		value := map[string]any{"type": kind, "required": false, "read_only": false, "label": label}
		for key, item := range extra {
			value[key] = item
		}
		return value
	}
	return map[string]any{"POST": map[string]any{
		"course_location_id": field("string", "Course location id", nil),
		"documents": field("field", "Documents", map[string]any{"read_only": true, "child": map[string]any{"type": "nested object",
			"required": false, "read_only": true, "children": map[string]any{
				"document_id": field("string", "Document id", nil), "creation_date": field("datetime", "Creation date", nil),
				"filepath":        field("string", "Filepath", map[string]any{"max_length": 1000}),
				"filename":        field("string", "Filename", map[string]any{"required": true, "max_length": 255}),
				"file_size_bytes": field("integer", "File size bytes", map[string]any{"min_value": 0, "max_value": int64(9223372036854775807)}),
				"token":           field("string", "Token", nil)}}}),
		"documents_data": field("list", "Documents data", map[string]any{"child": map[string]any{"type": "nested object", "required": true,
			"read_only": false, "child": map[string]any{"type": "field", "required": true, "read_only": false}}}),
		"title":             field("string", "Title", map[string]any{"max_length": 255}),
		"description":       field("string", "Description", map[string]any{"max_length": 2048}),
		"address":           field("string", "Address", map[string]any{"max_length": 255}),
		"sport_association": field("field", "Sport association", nil),
	}}
}()

// serve dispatches like CourseLocationViewSet.as_view({method: action}): the
// viewset's own delete() also answers DELETE on every mapped route.
func (h *CourseLocationWritesHandler) serve(c echo.Context, method, operation string, actions map[string]any,
	run func(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool, audit domain.LoginAuditContext) (int, any, error),
	remove func(ctx context.Context, identity domain.ResolvedIdentity, audit domain.LoginAuditContext) error) error {
	allow := method + ", DELETE, OPTIONS"
	if method == http.MethodDelete {
		allow = "DELETE, OPTIONS"
	}
	c.Response().Header().Set("Allow", allow)
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, method, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		if actions == nil {
			return supplierOptions(c, "Course Location", "")
		}
		return c.JSON(200, map[string]any{"name": "Course Location", "description": "", "renders": []string{"application/json"},
			"parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}, "actions": actions})
	}
	if request.Method != method && request.Method != http.MethodDelete {
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
	status, result := http.StatusNoContent, any(nil)
	if request.Method == http.MethodDelete {
		err = remove(ctx, *identity, audit)
	} else {
		status, result, err = run(ctx, *identity, body, media == "application/x-www-form-urlencoded" || media == "multipart/form-data", audit)
	}
	var invalid *service.CourseLocationValidationError
	switch {
	case errors.As(err, &invalid):
		return c.JSON(400, paymentCategoryValidationMessages(request, invalid.Fields))
	case errors.Is(err, domain.ErrCourseLocationForeignReference):
		return restError(c, &domain.PermissionDenied{Detail: messageReadsPermissionDetail(request)})
	case errors.Is(err, domain.ErrCourseLocationNotFound):
		return c.JSON(404, map[string]string{"detail": "No CourseLocation matches the given query."})
	case errors.Is(err, domain.ErrCourseLocationMalformed):
		return restError(c, &restInputError{Status: 404, Detail: restText(request, "notfound")})
	case errors.Is(err, service.ErrCourseLocationDocumentKey):
		return c.JSON(400, map[string]string{"exception": "document key not present"})
	case err != nil:
		return messageReadsError(c, err)
	}
	if status == http.StatusNoContent {
		return c.NoContent(status)
	}
	return c.JSON(status, result)
}

func (h *CourseLocationWritesHandler) Add(c echo.Context) error {
	return h.serve(c, http.MethodPost, "course_location_add", courseLocationAddActions,
		func(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool, audit domain.LoginAuditContext) (int, any, error) {
			item, err := h.writes.Add(ctx, identity, body, form, audit)
			return http.StatusCreated, item, err
		},
		func(context.Context, domain.ResolvedIdentity, domain.LoginAuditContext) error {
			// get_object() without a pk.
			return errCourseLocationNoPK
		})
}

func (h *CourseLocationWritesHandler) Update(c echo.Context) error {
	pk := c.Param("pk")
	return h.serve(c, http.MethodPatch, "course_location_update", nil,
		func(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool, audit domain.LoginAuditContext) (int, any, error) {
			item, err := h.writes.Update(ctx, identity, pk, body, form, audit)
			return http.StatusOK, item, err
		},
		func(ctx context.Context, identity domain.ResolvedIdentity, audit domain.LoginAuditContext) error {
			return h.writes.Delete(ctx, identity, pk, audit)
		})
}

func (h *CourseLocationWritesHandler) Delete(c echo.Context) error {
	pk := c.Param("pk")
	return h.serve(c, http.MethodDelete, "course_location_delete", nil, nil,
		func(ctx context.Context, identity domain.ResolvedIdentity, audit domain.LoginAuditContext) error {
			return h.writes.Delete(ctx, identity, pk, audit)
		})
}
