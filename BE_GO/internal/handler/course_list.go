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
	case errors.Is(err, service.ErrCourseListExport):
		return courseListExport(c)
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, body)
}

// courseListExport is the POST branch: request.data (a parse error is DRF's
// 400), then current_view_course. A format other than pdf or excel is
// Django's own 500; generating the PDF or XLSX file is not ported and is
// refused with the same 500 page before any work.
func courseListExport(c echo.Context) error {
	data, err := readMessageMutationsBody(c)
	if err != nil {
		return restError(c, err)
	}
	if object, ok := data.(domain.RequestObject); ok {
		if format, _ := object.Lookup("format"); format == "pdf" || format == "excel" {
			return messageReadsError(c, service.ErrCourseListExportUnported)
		}
	}
	// data['format'] is missing or not a supported format, or data is not a
	// dict: current_view_course raises Exception('Error in parsing data').
	return messageReadsError(c, service.ErrCourseListExport)
}
