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

type CourseEnrolmentService interface {
	Add(ctx context.Context, identity domain.ResolvedIdentity, request service.CourseEnrolmentRequest) ([]map[string]any, error)
	OverviewAdd(ctx context.Context, identity domain.ResolvedIdentity, course, subscription string, request service.CourseEnrolmentRequest) (map[string]any, error)
	MakePayment(ctx context.Context, identity domain.ResolvedIdentity, installment string, request service.CourseEnrolmentRequest) (map[string]any, error)
}

// CourseEnrolmentHandler serves course-subscriptions/add, the course-overview
// add and the installment payment.
type CourseEnrolmentHandler struct {
	enrolment     CourseEnrolmentService
	subscriptions CourseSubscriptionService
	actors        *service.ActorAuthentication
	identities    *service.IdentityResolver
	audit         LoginAuditReader
	operations    *paymentcategorylifecycle.Lifecycle
}

func NewCourseEnrolmentHandler(enrolment CourseEnrolmentService, subscriptions CourseSubscriptionService, actors *service.ActorAuthentication,
	identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *CourseEnrolmentHandler {
	return &CourseEnrolmentHandler{enrolment: enrolment, subscriptions: subscriptions, actors: actors, identities: identities, audit: audit,
		operations: operations}
}

const courseOverviewAddDoc = "API endpoint to add a new list of users to a course\n:param request: data: {}\n:param uid: course_id\n" +
	":param uid_subscription: subscription_id\n:return: token, refresh token"

func (h *CourseEnrolmentHandler) failure(c echo.Context, err error) error {
	request := c.Request()
	var invalidUUID *service.PaymentInfoUUIDError
	var invalid *service.CourseEnrolmentInvalid
	var invalidEvent *service.CourseWritesEventInvalid
	var denied *domain.PermissionDenied
	switch {
	case errors.As(err, &invalidUUID):
		return c.JSON(400, []string{invalidUUID.Detail})
	case errors.As(err, &invalid):
		if invalid.Many {
			items := make([]any, len(invalid.Items))
			for index, fields := range invalid.Items {
				items[index] = courseSubscriptionMessages(request, fields)
			}
			return c.JSON(400, items)
		}
		return c.JSON(400, courseSubscriptionMessages(request, invalid.Fields))
	case errors.As(err, &invalidEvent):
		return c.JSON(400, courseWritesMessages(request, invalidEvent.Fields))
	case errors.Is(err, domain.ErrCourseWritesForeign):
		return restError(c, &domain.PermissionDenied{Detail: "User not allowed."})
	case errors.As(err, &denied):
		return restError(c, err)
	}
	// Every other failure is an unhandled exception in Django (or a refused
	// input that is not ported): the 500 page.
	return restError(c, domain.ErrCourseSubscriptionServer)
}

func (h *CourseEnrolmentHandler) write(c echo.Context, run func(context.Context, service.CourseEnrolmentRequest) (any, error), status int) error {
	admitted, release, err := admitRESTWrite(c, h.operations, h.audit)
	if admitted == nil {
		return err
	}
	defer release()
	result, err := run(admitted.Context, service.CourseEnrolmentRequest{Body: admitted.Body, Form: admitted.Form,
		GroupHeader: c.Request().Header.Get("X-Group-Id"), Audit: admitted.Audit})
	if err != nil {
		return h.failure(c, err)
	}
	return c.JSON(status, result)
}

// Add serves course-subscriptions/add (as_view({'post': 'add'})); the
// viewset's own delete() answers DELETE without a pk.
func (h *CourseEnrolmentHandler) Add(c echo.Context) error {
	c.Response().Header().Set("Allow", "POST, DELETE, OPTIONS")
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, courseSubscriptionOperation)
	if identity == nil {
		return err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return c.JSON(200, map[string]any{"name": "Course Subscription", "description": "", "renders": []string{"application/json"},
			"parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}, "actions": courseSubscriptionListActions})
	case http.MethodDelete:
		return h.failure(c, h.subscriptions.DeleteWithoutPK(request.Context(), *identity))
	case http.MethodPost:
		return h.write(c, func(ctx context.Context, input service.CourseEnrolmentRequest) (any, error) {
			return h.enrolment.Add(ctx, *identity, input)
		}, http.StatusCreated)
	}
	return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
}

func (h *CourseEnrolmentHandler) serve(c echo.Context, operation, name, description string,
	run func(context.Context, domain.ResolvedIdentity, service.CourseEnrolmentRequest) (any, error)) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, name, description)
	}
	if request.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	return h.write(c, func(ctx context.Context, input service.CourseEnrolmentRequest) (any, error) {
		return run(ctx, *identity, input)
	}, http.StatusOK)
}

func (h *CourseEnrolmentHandler) OverviewAdd(c echo.Context) error {
	return h.serve(c, "course_overview_add", "Course Overview Add", courseOverviewAddDoc,
		func(ctx context.Context, identity domain.ResolvedIdentity, input service.CourseEnrolmentRequest) (any, error) {
			return h.enrolment.OverviewAdd(ctx, identity, c.Param("uid"), c.Param("uid_subscription"), input)
		})
}

func (h *CourseEnrolmentHandler) MakePayment(c echo.Context) error {
	return h.serve(c, "course_installment_make_payment", "Course Installment Make Payment", "",
		func(ctx context.Context, identity domain.ResolvedIdentity, input service.CourseEnrolmentRequest) (any, error) {
			return h.enrolment.MakePayment(ctx, identity, c.Param("uid"), input)
		})
}
