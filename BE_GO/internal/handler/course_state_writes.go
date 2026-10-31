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

type CourseStateWriteService interface {
	Enable(ctx context.Context, identity domain.ResolvedIdentity, raw, group string, audit domain.LoginAuditContext) (string, error)
	Disable(ctx context.Context, identity domain.ResolvedIdentity, raw, group string, audit domain.LoginAuditContext) (string, error)
	Pin(ctx context.Context, identity domain.ResolvedIdentity, raw, group string, audit domain.LoginAuditContext) (string, error)
}

type CourseStateWritesHandler struct {
	writes     CourseStateWriteService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewCourseStateWritesHandler(writes CourseStateWriteService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *CourseStateWritesHandler {
	return &CourseStateWritesHandler{writes: writes, actors: actors, identities: identities, audit: audit, operations: operations}
}

func (h *CourseStateWritesHandler) serve(c echo.Context, operation, name string,
	run func(ctx context.Context, identity domain.ResolvedIdentity, raw, group string, audit domain.LoginAuditContext) (string, error)) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, name, "")
	}
	if request.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	msg, err := run(write.Context, *identity, c.Param("uid"), request.Header.Get("X-Group-Id"), write.Audit)
	var invalid *service.PaymentInfoUUIDError
	switch {
	case errors.As(err, &invalid):
		return c.JSON(400, []string{invalid.Detail})
	case errors.Is(err, domain.ErrCourseStateNotFound):
		return c.JSON(404, map[string]string{"msg": "Course not found."})
	case errors.Is(err, domain.ErrCourseStateForeign):
		return restError(c, &domain.PermissionDenied{Detail: "User not allowed."})
	case errors.Is(err, domain.ErrCourseStateUnchanged):
		return restError(c, &domain.PermissionDenied{Detail: "User cannot change the status of the subscription."})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, map[string]any{"data": map[string]string{"msg": msg}})
}

func (h *CourseStateWritesHandler) Enable(c echo.Context) error {
	return h.serve(c, "course_enable", "Course Enable", h.writes.Enable)
}

func (h *CourseStateWritesHandler) Disable(c echo.Context) error {
	return h.serve(c, "course_disable", "Course Disable", h.writes.Disable)
}

func (h *CourseStateWritesHandler) Pin(c echo.Context) error {
	return h.serve(c, "course_pin", "Course Pin", h.writes.Pin)
}
