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

type CourseTagWriteService interface {
	Add(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) (map[string]any, error)
	Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, audit domain.LoginAuditContext) (map[string]any, error)
	Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error
	Link(ctx context.Context, identity domain.ResolvedIdentity, tag, course, group string, linked bool) error
}

type CourseTagWritesHandler struct {
	writes     CourseTagWriteService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewCourseTagWritesHandler(writes CourseTagWriteService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *CourseTagWritesHandler {
	return &CourseTagWritesHandler{writes: writes, actors: actors, identities: identities, audit: audit, operations: operations}
}

const courseTagsDocstring = ":param request: tag_name\n:return: list of tags"

func (h *CourseTagWritesHandler) serve(c echo.Context, method, operation, name, description string,
	run func(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) (any, error)) error {
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
	case errors.Is(err, service.ErrCourseTagNameRequired):
		return c.JSON(400, map[string]string{"msg": "tag_name is required"})
	case errors.Is(err, service.ErrCourseTagAthlete):
		return restError(c, &domain.PermissionDenied{Detail: "User is not a sport association"})
	case errors.Is(err, domain.ErrCourseTagNotFound):
		return c.JSON(404, map[string]string{"msg": "tag not found"})
	case errors.Is(err, domain.ErrCourseTagCourseNotFound):
		return c.JSON(404, map[string]string{"msg": "course not found"})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, result)
}

func (h *CourseTagWritesHandler) Add(c echo.Context) error {
	return h.serve(c, http.MethodPost, "course_tags_add", "Course Tags Add", "API endpoint to add a new tag for the subscriptions\n"+courseTagsDocstring,
		func(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) (any, error) {
			return h.writes.Add(ctx, identity, body, audit)
		})
}

func (h *CourseTagWritesHandler) Update(c echo.Context) error {
	tag := c.Param("tag_id")
	return h.serve(c, http.MethodPatch, "course_tags_update", "Course Tags Update", "API endpoint to update a tag for the subscriptions\n"+courseTagsDocstring,
		func(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) (any, error) {
			return h.writes.Update(ctx, identity, tag, body, audit)
		})
}

func (h *CourseTagWritesHandler) Delete(c echo.Context) error {
	tag := c.Param("tag_id")
	return h.serve(c, http.MethodDelete, "course_tags_delete", "Course Tags Delete", "API endpoint to delete a tag for the subscriptions\n"+courseTagsDocstring,
		func(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) (any, error) {
			if err := h.writes.Delete(ctx, identity, tag, audit); err != nil {
				return nil, err
			}
			return map[string]string{"msg": "tag deleted"}, nil
		})
}

func (h *CourseTagWritesHandler) link(c echo.Context, operation, name, description string, linked bool) error {
	tag, course := c.Param("tag_id"), c.Param("course_id")
	return h.serve(c, http.MethodPatch, operation, name, description,
		func(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) (any, error) {
			if err := h.writes.Link(ctx, identity, tag, course, c.Request().Header.Get("X-Group-Id"), linked); err != nil {
				return nil, err
			}
			// unassign answers the assign message too.
			return map[string]string{"msg": "tag assigned"}, nil
		})
}

func (h *CourseTagWritesHandler) Assign(c echo.Context) error {
	return h.link(c, "course_tags_assign", "Course Tags Assign", "API endpoint to update a tag for the subscriptions\n:param request: tag_id, subscription_id\n:return: list of tags", true)
}

func (h *CourseTagWritesHandler) Unassign(c echo.Context) error {
	return h.link(c, "course_tags_unassign", "Course Tags Unassign", "API endpoint to update a tag for the subscriptions\n:param request: tag_id, subscription_id\n:return: list of tags", false)
}
