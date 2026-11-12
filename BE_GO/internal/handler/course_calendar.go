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

type CourseCalendarService interface {
	Calendar(ctx context.Context, identity domain.ResolvedIdentity, raw, group string) (any, error)
	UpdatePost(ctx context.Context, identity domain.ResolvedIdentity, raw, group string, body any, bodyErr error, audit domain.LoginAuditContext) (any, error)
	UpdateDelete(ctx context.Context, identity domain.ResolvedIdentity, raw, group string, body any, bodyErr error, audit domain.LoginAuditContext) (any, error)
	Events(ctx context.Context, identity domain.ResolvedIdentity, query service.CalendarEventsQuery, group string, audit domain.LoginAuditContext) (any, error)
	GlobalUpdate(ctx context.Context, identity domain.ResolvedIdentity, body any, bodyErr error, audit domain.LoginAuditContext) (any, error)
}

// CourseCalendarHandler serves course/<uid>/calendar, course/<uid>/calendar/update,
// calendar/events and calendar/events/update (attendee_views.py).
type CourseCalendarHandler struct {
	calendars  CourseCalendarService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewCourseCalendarHandler(calendars CourseCalendarService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *CourseCalendarHandler {
	return &CourseCalendarHandler{calendars: calendars, actors: actors, identities: identities, audit: audit, operations: operations}
}

func courseCalendarRespond(c echo.Context, body any, err error) error {
	var invalid *service.PaymentInfoUUIDError
	var validation *domain.CalendarValidation
	var denied *domain.CalendarDenied
	var answer *domain.CalendarAnswer
	switch {
	case err == nil:
		return c.Blob(200, echo.MIMEApplicationJSON, dto.PythonJSONDumpsCompact(body))
	case errors.As(err, &invalid):
		return c.JSON(400, []string{invalid.Detail})
	case errors.Is(err, domain.ErrCalendarCourseNotFound):
		return c.JSON(404, map[string]string{"detail": err.Error()})
	case errors.As(err, &validation):
		return c.JSON(400, []string{validation.Detail})
	case errors.As(err, &denied):
		return restError(c, &domain.PermissionDenied{Detail: denied.Detail})
	case errors.As(err, &answer):
		return c.Blob(answer.Status, echo.MIMEApplicationJSON, dto.PythonJSONDumpsCompact(answer.Body))
	}
	// Unhandled view exceptions and refused (not ported) inputs are the 500 page.
	return restError(c, err)
}

// courseCalendarAssociationUser is IsAssociationUser, checked before method
// dispatch; denied=true means the 403 was written and the caller returns err.
func courseCalendarAssociationUser(c echo.Context, identity *domain.ResolvedIdentity) (denied bool, err error) {
	if role := identity.ScopeUser.Role; role != domain.RoleAssociation && role != domain.RoleCollaborator {
		return true, restError(c, &domain.PermissionDenied{Detail: messageReadsPermissionDetail(c.Request())})
	}
	return false, nil
}

// courseCalendarAdmit tracks and admits a write; DRF parses the body lazily,
// so a body error is handed to the service to raise where the view reads it.
func (h *CourseCalendarHandler) courseCalendarAdmit(c echo.Context, readBody bool,
	run func(ctx context.Context, body any, bodyErr error, audit domain.LoginAuditContext) (any, error)) error {
	request := c.Request()
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	var body any
	var bodyErr error
	if readBody {
		body, bodyErr = readMessageMutationsBody(c)
	}
	audit, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	result, err := run(ctx, body, bodyErr, audit)
	return courseCalendarRespond(c, result, err)
}

// Calendar is AllowAny: an anonymous request has no scope user.
func (h *CourseCalendarHandler) Calendar(c echo.Context) error {
	identity, err := messageMutationsIdentityFor(c, h.actors, h.identities, http.MethodGet, "calendar", true)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Calendar", "API endpoint to get the calendar for the current course")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	body, err := h.calendars.Calendar(request.Context(), *identity, c.Param("uid"), request.Header.Get("X-Group-Id"))
	return courseCalendarRespond(c, body, err)
}

func (h *CourseCalendarHandler) CalendarUpdate(c echo.Context) error {
	c.Response().Header().Set("Allow", "POST, DELETE, OPTIONS")
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "calendar_update")
	if identity == nil {
		return err
	}
	if denied, err := courseCalendarAssociationUser(c, identity); denied {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Calendar Update", "API endpoint to update the calendar of a course")
	}
	uid, group := c.Param("uid"), request.Header.Get("X-Group-Id")
	switch request.Method {
	case http.MethodPost:
		return h.courseCalendarAdmit(c, true, func(ctx context.Context, body any, bodyErr error, audit domain.LoginAuditContext) (any, error) {
			return h.calendars.UpdatePost(ctx, *identity, uid, group, body, bodyErr, audit)
		})
	case http.MethodDelete:
		return h.courseCalendarAdmit(c, true, func(ctx context.Context, body any, bodyErr error, audit domain.LoginAuditContext) (any, error) {
			return h.calendars.UpdateDelete(ctx, *identity, uid, group, body, bodyErr, audit)
		})
	}
	return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
}

func courseCalendarQueryValue(query domain.RequestObject, name string) *string {
	value, ok := query.Lookup(name)
	if !ok {
		return nil
	}
	text, _ := value.(string)
	return &text
}

func (h *CourseCalendarHandler) Events(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, "full_events_calendar")
	if identity == nil {
		return err
	}
	if denied, err := courseCalendarAssociationUser(c, identity); denied {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Full Events Calendar", "API endpoint to get the attendance registry for the current course")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	query := parseReferenceForm(request.URL.RawQuery)
	values := service.CalendarEventsQuery{Start: courseCalendarQueryValue(query, "start"), End: courseCalendarQueryValue(query, "end"),
		GetLesson: courseCalendarQueryValue(query, "get_lesson"), EventID: courseCalendarQueryValue(query, "event_id")}
	group := request.Header.Get("X-Group-Id")
	// The view may create the association's global calendar row.
	return h.courseCalendarAdmit(c, false, func(ctx context.Context, _ any, _ error, audit domain.LoginAuditContext) (any, error) {
		return h.calendars.Events(ctx, *identity, values, group, audit)
	})
}

func (h *CourseCalendarHandler) EventsUpdate(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "full_events_calendar_update")
	if identity == nil {
		return err
	}
	if denied, err := courseCalendarAssociationUser(c, identity); denied {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Full Events Calendar Update", "")
	}
	if request.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	return h.courseCalendarAdmit(c, true, func(ctx context.Context, body any, bodyErr error, audit domain.LoginAuditContext) (any, error) {
		return h.calendars.GlobalUpdate(ctx, *identity, body, bodyErr, audit)
	})
}
