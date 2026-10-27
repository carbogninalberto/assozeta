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

type AttendanceDayService interface {
	Delete(context.Context, domain.ResolvedIdentity, string, domain.LoginAuditContext) error
}

type AttendanceMarkService interface {
	Mark(context.Context, domain.ResolvedIdentity, string, any, domain.LoginAuditContext) (string, error)
}

type AttendanceDaysHandler struct {
	days       AttendanceDayService
	marks      AttendanceMarkService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewAttendanceDaysHandler(days AttendanceDayService, marks AttendanceMarkService, actors *service.ActorAuthentication, identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *AttendanceDaysHandler {
	return &AttendanceDaysHandler{days: days, marks: marks, actors: actors, identities: identities, audit: audit, operations: operations}
}

func (h *AttendanceDaysHandler) Delete(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodDelete, "attendance_day_delete")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, "Attendance Day Delete")
	}
	if request.Method != http.MethodDelete {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
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
	err = h.days.Delete(ctx, *identity, c.Param("uid"), audit)
	if errors.Is(err, domain.ErrAttendanceDayNotFound) {
		// The view returns a set literal, which DRF renders as a list.
		return c.JSON(404, []string{err.Error()})
	}
	if err != nil {
		return messageReadsError(c, err)
	}
	return c.JSON(200, map[string]any{"data": map[string]string{"msg": "attendance day deleted"}})
}

func (h *AttendanceDaysHandler) MarkAbsent(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "attendance_day_mark_absent")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, "Attendance Day Mark Absent")
	}
	if request.Method != http.MethodPost {
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
	message, err := h.marks.Mark(ctx, *identity, c.Param("uid"), body, audit)
	var listed *service.AttendanceAlreadyListed
	switch {
	case errors.As(err, &listed):
		return c.JSON(200, []string{err.Error()})
	case errors.Is(err, domain.ErrAttendanceDayNotFound):
		return c.JSON(404, []string{err.Error()})
	case errors.Is(err, service.ErrAttendanceSubscriptionRequired):
		return c.JSON(400, []string{err.Error()})
	case errors.Is(err, service.ErrAttendanceSubscriptionDenied):
		return restError(c, &domain.PermissionDenied{Detail: err.Error()})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, map[string]any{"data": map[string]string{"msg": message}})
}
