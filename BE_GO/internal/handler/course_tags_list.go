package handler

import (
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type CourseTagListHandler struct {
	lists      *service.CourseTagLists
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewCourseTagListHandler(lists *service.CourseTagLists, actors *service.ActorAuthentication, identities *service.IdentityResolver) *CourseTagListHandler {
	return &CourseTagListHandler{lists: lists, actors: actors, identities: identities}
}

func (h *CourseTagListHandler) List(c echo.Context) error {
	identity, err := messageReadsIdentity(c, h.actors, h.identities, http.MethodGet, "course_tags_list")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Course Tags List", "API endpoint to get the list of tags for the subscriptions\n:param request: None\n:return: list of tags")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	body, err := h.lists.List(request.Context(), *identity)
	if err != nil {
		return messageReadsError(c, err)
	}
	return c.JSON(200, body)
}

type CourseLocationListHandler struct {
	lists      *service.CourseLocationLists
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewCourseLocationListHandler(lists *service.CourseLocationLists, actors *service.ActorAuthentication, identities *service.IdentityResolver) *CourseLocationListHandler {
	return &CourseLocationListHandler{lists: lists, actors: actors, identities: identities}
}

// List mirrors CourseLocationViewSet.as_view({'get': 'list'}): the viewset's
// delete() also answers DELETE here, where get_object() has no pk (500).
func (h *CourseLocationListHandler) List(c echo.Context) error {
	c.Response().Header().Set("Allow", "GET, HEAD, DELETE, OPTIONS")
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, "course_location_list")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Course Location", "")
	}
	switch request.Method {
	case http.MethodGet, http.MethodHead:
	case http.MethodDelete:
		return restError(c, errCourseLocationNoPK)
	default:
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	body, err := h.lists.List(request.Context(), *identity)
	if err != nil {
		return messageReadsError(c, err)
	}
	return c.JSON(200, body)
}

var errCourseLocationNoPK = errors.New("course location delete without a lookup key")
