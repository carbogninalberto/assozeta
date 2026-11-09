package handler

import (
	"context"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/labstack/echo/v4"
)

// CourseLocationRetriever is the router detail route's retrieve.
type CourseLocationRetriever interface {
	Retrieve(ctx context.Context, identity domain.ResolvedIdentity, raw string) (map[string]any, error)
}

// RegisterCourseLocations adds router.register(r'course-locations',
// CourseLocationViewSet): list() and the add route's create, the stock
// retrieve and destroy, update() (always partial) for PUT.
func (h *RouterStockHandler) RegisterCourseLocations(lists *CourseLocationListHandler, writes *CourseLocationWritesHandler) {
	service := writes.writes
	retriever := service.(CourseLocationRetriever)
	h.register(&routerStockViewset{
		prefix: "course-locations", name: "Course Location",
		formatted: map[string]bool{"create": true, "retrieve": true, "destroy": true},
		list: func(c echo.Context, identity *domain.ResolvedIdentity, _ string) error {
			body, err := lists.lists.List(c.Request().Context(), *identity)
			if err != nil {
				return messageReadsError(c, err)
			}
			return c.JSON(200, body)
		},
		create: func(c echo.Context, identity *domain.ResolvedIdentity, _ string) error {
			return writes.write(c, func(write *restWrite) (int, any, error) {
				item, err := service.Add(write.Context, *identity, write.Body, write.Form, write.Audit)
				return http.StatusCreated, item, err
			})
		},
		retrieve: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) error {
			item, err := retriever.Retrieve(c.Request().Context(), *identity, pk)
			return writes.respond(c, http.StatusOK, item, err)
		},
		update: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) error {
			return writes.write(c, func(write *restWrite) (int, any, error) {
				item, err := service.Update(write.Context, *identity, pk, write.Body, write.Form, write.Audit)
				return http.StatusOK, item, err
			})
		},
		destroy: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) error {
			return writes.write(c, func(write *restWrite) (int, any, error) {
				return http.StatusNoContent, nil, service.Delete(write.Context, *identity, pk, write.Audit)
			})
		},
		postFields: func(echo.Context, *domain.ResolvedIdentity) (map[string]any, error) {
			return courseLocationAddActions["POST"].(map[string]any), nil
		},
		putFields: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) (map[string]any, error) {
			_, err := retriever.Retrieve(c.Request().Context(), *identity, pk)
			if errors.Is(err, domain.ErrCourseLocationNotFound) || errors.Is(err, domain.ErrCourseLocationMalformed) {
				return nil, nil
			}
			if err != nil {
				return nil, err
			}
			return courseLocationAddActions["POST"].(map[string]any), nil
		},
	})
}
