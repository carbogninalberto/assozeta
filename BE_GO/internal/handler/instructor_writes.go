package handler

import (
	"context"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type InstructorWritesService interface {
	Add(ctx context.Context, identity domain.ResolvedIdentity, load service.InstructorHoursBody, audit domain.LoginAuditContext) error
	Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, load service.InstructorHoursBody, audit domain.LoginAuditContext) error
	Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error
	Compensation(ctx context.Context, identity domain.ResolvedIdentity, raw string, load service.InstructorHoursBody, group string,
		audit domain.LoginAuditContext) error
	Lessons(ctx context.Context, identity domain.ResolvedIdentity, request service.InstructorLessonsRequest) (map[string]any, error)
}

// InstructorWritesHandler serves instructor add, update, delete,
// hours/add/compensation and lessons-hours.
type InstructorWritesHandler struct {
	writes InstructorWritesService
	hours  *InstructorHoursHandler
	report *service.InstructorReport
}

// WithReport serves instructor_report through the report service.
func (h *InstructorWritesHandler) WithReport(report *service.InstructorReport) *InstructorWritesHandler {
	h.report = report
	return h
}

// Report mirrors instructor_report (GET, IsAuthenticated; a read).
func (h *InstructorWritesHandler) Report(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.hours.actors, h.hours.identities, http.MethodGet, "instructor_report")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Instructor Report", "")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	query := parseReferenceForm(request.URL.RawQuery)
	param := func(name string) *string {
		value, ok := query.Lookup(name)
		text, isText := value.(string)
		if !ok || !isText {
			return nil
		}
		return &text
	}
	body, err := h.report.Report(request.Context(), *identity, param("start_date"), param("end_date"))
	switch {
	case err == nil:
		return c.Blob(http.StatusOK, echo.MIMEApplicationJSON, dto.PythonJSONDumpsCompact(body))
	case errors.Is(err, service.InstructorReportMissingDates):
		return c.JSON(400, map[string]string{"msg": err.Error()})
	}
	return messageReadsError(c, err)
}

func NewInstructorWritesHandler(writes InstructorWritesService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *InstructorWritesHandler {
	return &InstructorWritesHandler{writes: writes, hours: &InstructorHoursHandler{actors: actors, identities: identities, audit: audit,
		operations: operations}}
}

// instructorLessonsDescription is the view docstring DRF's OPTIONS renders.
const instructorLessonsDescription = "Lesson hours computed from the course calendar for one instructor (uid)\n" +
	"or for all instructors of the current association (no uid).\n\nAuthorization:\n" +
	"    - association admins and collaborators may query any instructor\n      of their association or the whole list\n" +
	"    - a user tied to an instructor profile (Instructor.associated_user_id)\n      may only query their own hours\n\n" +
	"Query params:\n    start_date, end_date: optional Europe/Rome DD/MM/YYYY period filter\n" +
	"    include_lessons: true adds event details in single-instructor mode"

func (h *InstructorWritesHandler) failure(c echo.Context, request *instructorHoursRequest, err error) error {
	var invalid *service.InstructorWritesValidationError
	var answer *service.InstructorLessonsAnswer
	switch {
	case request != nil && request.bodyError != nil && errors.Is(err, request.bodyError):
		return restError(c, err)
	case errors.As(err, &invalid):
		return c.JSON(400, instructorHoursMessages(c.Request(), invalid.Fields))
	case errors.As(err, &answer):
		return c.JSON(answer.Status, map[string]string{"error": answer.Message})
	case errors.Is(err, service.ErrInstructorNotFound):
		return c.JSON(404, map[string]string{"error": "instructor not found"})
	case errors.Is(err, service.ErrInstructorHoursMissing):
		return c.JSON(400, map[string]string{"error": "hours not found"})
	}
	// Malformed uids (400 list), Django's exceptions and refused input (500 page).
	return messageReadsError(c, err)
}

func (h *InstructorWritesHandler) serve(c echo.Context, method, operation, name, description string,
	run func(ctx context.Context, request *instructorHoursRequest) error) error {
	identity, err := messageMutationsIdentity(c, h.hours.actors, h.hours.identities, method, operation)
	if identity == nil {
		return err
	}
	httpRequest := c.Request()
	if httpRequest.Method == http.MethodOptions {
		return supplierOptions(c, name, description)
	}
	if httpRequest.Method != method {
		return restError(c, &restInputError{Status: 405, Detail: restText(httpRequest, "method")})
	}
	tracked, err := h.hours.operations.Track(httpRequest.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	auditContext, err := h.hours.audit(httpRequest)
	if err != nil {
		return restError(c, err)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	request := &instructorHoursRequest{c: c, identity: *identity, audit: auditContext}
	if err := run(ctx, request); err != nil {
		return h.failure(c, request, err)
	}
	return nil
}

func (h *InstructorWritesHandler) Add(c echo.Context) error {
	return h.serve(c, http.MethodPost, "instructor_add", "Instructor Add", "", func(ctx context.Context, r *instructorHoursRequest) error {
		if err := h.writes.Add(ctx, r.identity, r.Body, r.audit); err != nil {
			return err
		}
		return c.JSON(200, map[string]string{"msg": "instructor created"})
	})
}

func (h *InstructorWritesHandler) Update(c echo.Context) error {
	return h.serve(c, http.MethodPatch, "instructor_update", "Instructor Update", "", func(ctx context.Context, r *instructorHoursRequest) error {
		if err := h.writes.Update(ctx, r.identity, c.Param("uid"), r.Body, r.audit); err != nil {
			return err
		}
		return c.JSON(200, map[string]string{"msg": "instructor updated"})
	})
}

func (h *InstructorWritesHandler) Delete(c echo.Context) error {
	return h.serve(c, http.MethodDelete, "instructor_delete", "Instructor Delete", "", func(ctx context.Context, r *instructorHoursRequest) error {
		if err := h.writes.Delete(ctx, r.identity, c.Param("uid"), r.audit); err != nil {
			return err
		}
		return c.JSON(200, map[string]string{"msg": "instructor deleted"})
	})
}

func (h *InstructorWritesHandler) Compensation(c echo.Context) error {
	return h.serve(c, http.MethodPost, "instructor_hours_add_compensation", "Instructor Hours Add Compensation", "",
		func(ctx context.Context, r *instructorHoursRequest) error {
			if err := h.writes.Compensation(ctx, r.identity, c.Param("uid"), r.Body, c.Request().Header.Get("X-Group-Id"), r.audit); err != nil {
				return err
			}
			return c.JSON(200, map[string]string{"msg": "compensation created."})
		})
}

func (h *InstructorWritesHandler) Lessons(c echo.Context) error {
	return h.serve(c, http.MethodGet, "instructor_lessons_hours", "Instructor Lessons Hours", instructorLessonsDescription,
		func(ctx context.Context, r *instructorHoursRequest) error {
			query := parseReferenceForm(c.Request().URL.RawQuery)
			text := func(name string) string {
				value, _ := query.Lookup(name)
				result, _ := value.(string)
				return result
			}
			request := service.InstructorLessonsRequest{StartDate: text("start_date"), EndDate: text("end_date"),
				IncludeLessons: text("include_lessons") == "true"}
			if uid := c.Param("uid"); uid != "" {
				request.UID = &uid
			}
			body, err := h.writes.Lessons(ctx, r.identity, request)
			if err != nil {
				return err
			}
			return c.JSON(200, body)
		})
}
