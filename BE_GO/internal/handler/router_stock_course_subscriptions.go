package handler

import (
	"context"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type courseSubscriptionRetriever interface {
	Retrieve(ctx context.Context, identity domain.ResolvedIdentity, raw string, request service.CourseSubscriptionRequest) (map[string]any, error)
}

type courseEnrolmentStockCreator interface {
	StockCreate(ctx context.Context, identity domain.ResolvedIdentity, request service.CourseEnrolmentRequest) error
}

// RegisterCourseSubscriptions adds router.register(r'course-subscriptions',
// CourseSubscriptionViewSet): list(), the stock create (perform_create with
// is_athlete=False, not atomic, then a 500 from rendering the validated
// data), the stock retrieve and destroy through the viewset's get_object()
// (live rows only), update() for PUT.
func (h *RouterStockHandler) RegisterCourseSubscriptions(subscriptions *CourseSubscriptionsHandler, enrolment *CourseEnrolmentHandler) {
	retriever := subscriptions.subscriptions.(courseSubscriptionRetriever)
	creator := enrolment.enrolment.(courseEnrolmentStockCreator)
	fields := courseSubscriptionListActions["POST"].(map[string]any)
	h.register(&routerStockViewset{
		prefix: "course-subscriptions", name: "Course Subscription",
		formatted: map[string]bool{"create": true, "retrieve": true, "destroy": true},
		list: func(c echo.Context, identity *domain.ResolvedIdentity, _ string) error {
			result, err := subscriptions.subscriptions.List(c.Request().Context(), *identity, subscriptions.request(c), false)
			if err != nil {
				return subscriptions.failure(c, err)
			}
			return c.JSON(http.StatusOK, result)
		},
		create: func(c echo.Context, identity *domain.ResolvedIdentity, _ string) error {
			return enrolment.write(c, func(ctx context.Context, input service.CourseEnrolmentRequest) (any, error) {
				return nil, creator.StockCreate(ctx, *identity, input)
			}, http.StatusCreated)
		},
		retrieve: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) error {
			item, err := retriever.Retrieve(c.Request().Context(), *identity, pk, subscriptions.request(c))
			if err != nil {
				return subscriptions.failure(c, err)
			}
			return c.JSON(http.StatusOK, item)
		},
		update: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) error {
			admitted, release, err := admitRESTWrite(c, subscriptions.operations, subscriptions.audit)
			if admitted == nil {
				return err
			}
			defer release()
			result, err := subscriptions.subscriptions.Update(admitted.Context, *identity, pk, subscriptions.request(c), admitted.Body, admitted.Form,
				admitted.Audit)
			if err != nil {
				return subscriptions.failure(c, err)
			}
			return c.JSON(http.StatusOK, result)
		},
		destroy: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) error {
			return subscriptions.removePK(c, *identity, pk, false)
		},
		listDelete: func(c echo.Context, identity *domain.ResolvedIdentity, _ string) error {
			return subscriptions.failure(c, subscriptions.subscriptions.DeleteWithoutPK(c.Request().Context(), *identity))
		},
		postFields: func(echo.Context, *domain.ResolvedIdentity) (map[string]any, error) { return fields, nil },
		putFields: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) (map[string]any, error) {
			_, err := retriever.Retrieve(c.Request().Context(), *identity, pk, subscriptions.request(c))
			var denied *domain.PermissionDenied
			if errors.Is(err, domain.ErrCourseSubscriptionNotFound) || errors.As(err, &denied) {
				return nil, nil
			}
			if err != nil {
				return nil, err
			}
			return fields, nil
		},
	})
}
