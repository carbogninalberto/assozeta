package handler

import (
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type CourseOverviewHandler struct {
	overviews  *service.CourseOverviews
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewCourseOverviewHandler(overviews *service.CourseOverviews, actors *service.ActorAuthentication, identities *service.IdentityResolver) *CourseOverviewHandler {
	return &CourseOverviewHandler{overviews: overviews, actors: actors, identities: identities}
}

// Overview mirrors course_overview (@api_view(['GET'])).
func (h *CourseOverviewHandler) Overview(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, "course_overview")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Course Overview", "")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	body, err := h.overviews.Overview(request.Context(), *identity, c.Param("uid"), request.Header.Get("X-Group-Id"))
	var invalid *service.PaymentInfoUUIDError
	switch {
	case errors.As(err, &invalid):
		return c.JSON(400, []string{invalid.Detail})
	case errors.Is(err, domain.ErrCourseOverviewNotFound):
		return restError(c, &domain.PermissionDenied{Detail: "Course not found"})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, body)
}
