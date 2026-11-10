package handler

import (
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type CourseListHandler struct {
	courses    *service.CourseLists
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewCourseListHandler(courses *service.CourseLists, actors *service.ActorAuthentication, identities *service.IdentityResolver) *CourseListHandler {
	return &CourseListHandler{courses: courses, actors: actors, identities: identities}
}

// List mirrors course_list (@api_view(['GET', 'POST'])).
func (h *CourseListHandler) List(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, "GET, POST", "course_list")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Course List", "")
	}
	if request.Method != http.MethodGet && request.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	body, err := h.courses.List(request.Context(), *identity, service.CourseListRequest{
		Query: parseReferenceForm(request.URL.RawQuery), GroupHeader: request.Header.Get("X-Group-Id"), Method: request.Method})
	var invalid *service.PaymentInfoUUIDError
	var export *service.CourseListExport
	switch {
	case errors.As(err, &invalid):
		return c.JSON(400, []string{invalid.Detail})
	case errors.Is(err, domain.ErrCourseListMissingAssociation):
		return restError(c, &domain.PermissionDenied{Detail: "Missing sport_association_id query param."})
	case errors.Is(err, dto.ErrDRFInvalidPage):
		detail := "Invalid page."
		if restLanguage(request) != "en" {
			detail = "Pagina non valida."
		}
		return c.JSON(404, map[string]string{"detail": detail})
	case errors.As(err, &export):
		return h.export(c, export)
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, body)
}

// export is the POST branch: request.data (a parse error is DRF's 400),
// then current_view_course. Its exceptions, and the PDF file and inputs Go
// does not reproduce, are the 500 page before any work.
func (h *CourseListHandler) export(c echo.Context, export *service.CourseListExport) error {
	data, err := readMessageMutationsBody(c)
	if err != nil {
		return restError(c, err)
	}
	body, err := h.courses.Export(c.Request().Context(), export, data)
	if err != nil {
		return messageReadsError(c, err)
	}
	return c.JSON(200, body)
}
