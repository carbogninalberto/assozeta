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

type CourseWriteService interface {
	Add(ctx context.Context, identity domain.ResolvedIdentity, request service.CourseWritesRequest) error
	Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, request service.CourseWritesRequest) error
	Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, request service.CourseWritesRequest) error
	OverviewUpdate(ctx context.Context, identity domain.ResolvedIdentity, course, subscription string, request service.CourseWritesRequest) error
	OverviewDelete(ctx context.Context, identity domain.ResolvedIdentity, course, subscription string, request service.CourseWritesRequest) error
}

type CourseWritesHandler struct {
	writes     CourseWriteService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewCourseWritesHandler(writes CourseWriteService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *CourseWritesHandler {
	return &CourseWritesHandler{writes: writes, actors: actors, identities: identities, audit: audit, operations: operations}
}

const (
	courseAddDoc            = "API endpoint to add a new course\n:param request: data: {}\n:return: token, refresh token"
	courseUpdateDoc         = "API endpoint to update a course\n:param request: data: {}\n:return: token, refresh token"
	courseOverviewUpdateDoc = "API endpoint to update a user in a course\n:param request: data: {}\n:param uid: course_id\n" +
		":param uid_subscription: subscription_id\n:return: token, refresh token"
)

// courseWritesMessages renders DRF's field errors, including the IntegerField
// maximum and the DateField format for DATE_INPUT_FORMATS ['%d/%m/%Y'].
func courseWritesMessages(request *http.Request, fields map[string][]string) any {
	english := restLanguage(request) == "en"
	translated := make(map[string]any, len(fields))
	for name, codes := range fields {
		messages := make([]string, len(codes))
		for index, code := range codes {
			messages[index] = code
			switch {
			case code == dto.CourseWritesDate:
				messages[index] = "La data è in un formato errato. Usa uno dei seguenti formati: DD/MM/YYYY."
				if english {
					messages[index] = "Date has wrong format. Use one of these formats instead: DD/MM/YYYY."
				}
			case strings.HasPrefix(code, dto.CourseWritesMaxValue):
				limit := strings.TrimPrefix(code, dto.CourseWritesMaxValue)
				messages[index] = "Assicurati che il valore sia minore o uguale a " + limit + "."
				if english {
					messages[index] = "Ensure this value is less than or equal to " + limit + "."
				}
			default:
				messages[index] = paymentCategoryValidationMessage(code, english)
			}
		}
		translated[name] = messages
	}
	return translated
}

func (h *CourseWritesHandler) serve(c echo.Context, method, operation, name, description string,
	run func(ctx context.Context, identity domain.ResolvedIdentity, request service.CourseWritesRequest) error, success any) error {
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
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	err = run(write.Context, *identity, service.CourseWritesRequest{Body: write.Body, GroupHeader: request.Header.Get("X-Group-Id"), Audit: write.Audit})
	var invalidUUID *service.PaymentInfoUUIDError
	var invalidAdd *dto.CourseAddInvalid
	var invalidEvent *service.CourseWritesEventInvalid
	switch {
	case err == nil:
		return c.JSON(200, success)
	case errors.As(err, &invalidUUID):
		return c.JSON(400, []string{invalidUUID.Detail})
	case errors.As(err, &invalidAdd):
		switch {
		case invalidAdd.Events != "":
			return c.JSON(400, map[string]any{"msg:": "not valid course information.", "details": map[string]any{"events": []string{invalidAdd.Events}}})
		case invalidAdd.Event != nil:
			return c.JSON(400, courseWritesMessages(request, invalidAdd.Event))
		}
		return c.JSON(400, courseWritesMessages(request, invalidAdd.Fields))
	case errors.As(err, &invalidEvent):
		return c.JSON(400, courseWritesMessages(request, invalidEvent.Fields))
	case errors.Is(err, domain.ErrCourseWritesNotFound):
		return c.JSON(404, map[string]string{"msg": "Course not found."})
	case errors.Is(err, domain.ErrCourseWritesUpdateNotFound):
		return c.JSON(400, map[string]string{"msg:": "course not found."})
	case errors.Is(err, domain.ErrCourseWritesBodyKeys):
		return c.JSON(400, map[string]string{"msg": "validation errors."})
	case errors.Is(err, domain.ErrCourseWritesForeign):
		return restError(c, &domain.PermissionDenied{Detail: "User not allowed."})
	}
	return messageReadsError(c, err)
}

func (h *CourseWritesHandler) Add(c echo.Context) error {
	return h.serve(c, http.MethodPost, "course_add", "Course Add", courseAddDoc, h.writes.Add, map[string]string{"status": "success"})
}

func (h *CourseWritesHandler) Update(c echo.Context) error {
	return h.serve(c, http.MethodPatch, "course_update", "Course Update", courseUpdateDoc,
		func(ctx context.Context, identity domain.ResolvedIdentity, request service.CourseWritesRequest) error {
			return h.writes.Update(ctx, identity, c.Param("uid"), request)
		}, map[string]string{"status": "success"})
}

func (h *CourseWritesHandler) Delete(c echo.Context) error {
	return h.serve(c, http.MethodPost, "course_delete", "Course Delete", "",
		func(ctx context.Context, identity domain.ResolvedIdentity, request service.CourseWritesRequest) error {
			return h.writes.Delete(ctx, identity, c.Param("uid"), request)
		}, map[string]any{"data": map[string]string{"msg": "course deleted."}})
}

func (h *CourseWritesHandler) OverviewUpdate(c echo.Context) error {
	return h.serve(c, http.MethodPost, "course_overview_update", "Course Overview Update", courseOverviewUpdateDoc,
		func(ctx context.Context, identity domain.ResolvedIdentity, request service.CourseWritesRequest) error {
			return h.writes.OverviewUpdate(ctx, identity, c.Param("uid"), c.Param("uid_subscription"), request)
		}, map[string]any{"data": map[string]string{"msg": "course subscription update."}})
}

func (h *CourseWritesHandler) OverviewDelete(c echo.Context) error {
	return h.serve(c, http.MethodDelete, "course_overview_delete", "Course Overview Delete", "",
		func(ctx context.Context, identity domain.ResolvedIdentity, request service.CourseWritesRequest) error {
			return h.writes.OverviewDelete(ctx, identity, c.Param("uid"), c.Param("uid_subscription"), request)
		}, map[string]any{"data": map[string]string{"msg": "associate deleted."}})
}
