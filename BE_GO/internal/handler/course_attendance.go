package handler

import (
	"context"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type CourseAttendanceService interface {
	Attendees(ctx context.Context, identity domain.ResolvedIdentity, raw, group string, audit domain.LoginAuditContext) (any, error)
	AttendeesUpdate(ctx context.Context, identity domain.ResolvedIdentity, raw, rawDay, group string, body any, bodyErr error,
		audit domain.LoginAuditContext) (any, error)
	SubscriptionAttendance(ctx context.Context, identity domain.ResolvedIdentity, raw string, course *string, group string) (any, error)
	Mark(ctx context.Context, identity domain.ResolvedIdentity, raw, group string, body any, bodyErr error, audit domain.LoginAuditContext) (any, error)
}

// CourseAttendanceHandler serves course/<uid>/attendees,
// course/<uid>/attendees/<attendance_day_uid>/update, subscription/<uid>/attendance
// and attendance/<uid>/mark.
type CourseAttendanceHandler struct {
	attendance CourseAttendanceService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewCourseAttendanceHandler(attendance CourseAttendanceService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *CourseAttendanceHandler {
	return &CourseAttendanceHandler{attendance: attendance, actors: actors, identities: identities, audit: audit, operations: operations}
}

// admit tracks and admits a view that may write (all four can); DRF parses
// the body lazily, so a body error is handed to the service.
func (h *CourseAttendanceHandler) admit(c echo.Context, readBody bool,
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

func (h *CourseAttendanceHandler) Attendees(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, "attendees")
	if identity == nil {
		return err
	}
	if denied, err := courseCalendarAssociationUser(c, identity); denied {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Attendees", "API endpoint to get the attendance registry for the current course")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	uid, group := c.Param("uid"), request.Header.Get("X-Group-Id")
	// Listing links unlinked days (auditlog entries).
	return h.admit(c, false, func(ctx context.Context, _ any, _ error, audit domain.LoginAuditContext) (any, error) {
		return h.attendance.Attendees(ctx, *identity, uid, group, audit)
	})
}

func (h *CourseAttendanceHandler) AttendeesUpdate(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "attendees_update")
	if identity == nil {
		return err
	}
	if denied, err := courseCalendarAssociationUser(c, identity); denied {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Attendees Update", "API endpoint to update an attendance registry event")
	}
	if request.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	uid, day, group := c.Param("uid"), c.Param("attendance_day_uid"), request.Header.Get("X-Group-Id")
	return h.admit(c, true, func(ctx context.Context, body any, bodyErr error, audit domain.LoginAuditContext) (any, error) {
		return h.attendance.AttendeesUpdate(ctx, *identity, uid, day, group, body, bodyErr, audit)
	})
}

func (h *CourseAttendanceHandler) SubscriptionAttendance(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, "subscription_attendance")
	if identity == nil {
		return err
	}
	// IsAssociationUser | IsAthleteUser.
	if role := identity.ScopeUser.Role; role != domain.RoleAssociation && role != domain.RoleCollaborator && role != domain.RoleAthlete {
		return restError(c, &domain.PermissionDenied{Detail: messageReadsPermissionDetail(c.Request())})
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Subscription Attendance", "API endpoint to get the attendance registry for the current course")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	var course *string
	if value, present := parseReferenceForm(request.URL.RawQuery).Lookup("course"); present {
		text, _ := value.(string)
		course = &text
	}
	body, err := h.attendance.SubscriptionAttendance(request.Context(), *identity, c.Param("uid"), course, request.Header.Get("X-Group-Id"))
	return courseCalendarRespond(c, body, err)
}

func (h *CourseAttendanceHandler) Mark(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "attendance_mark")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Attendance Mark", "")
	}
	if request.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	uid, group := c.Param("uid"), request.Header.Get("X-Group-Id")
	return h.admit(c, true, func(ctx context.Context, body any, bodyErr error, audit domain.LoginAuditContext) (any, error) {
		return h.attendance.Mark(ctx, *identity, uid, group, body, bodyErr, audit)
	})
}
