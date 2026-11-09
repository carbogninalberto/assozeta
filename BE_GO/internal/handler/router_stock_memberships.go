package handler

import (
	"context"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type subscriptionMembershipRouterService interface {
	Create(ctx context.Context, identity domain.ResolvedIdentity, request service.SubscriptionMembershipRequest, body any, form bool,
		audit domain.LoginAuditContext) (map[string]any, error)
	Retrieve(ctx context.Context, identity domain.ResolvedIdentity, raw string, request service.SubscriptionMembershipRequest) (map[string]any, error)
}

// RegisterMemberships adds router.register(r'subscription/memberships',
// SubscriptionMembershipViewSet): list(), the stock create (no association
// injection, unlike add), retrieve and destroy, update() (partial) for PUT.
func (h *RouterStockHandler) RegisterMemberships(memberships *SubscriptionMembershipsHandler) {
	stock := memberships.memberships.(subscriptionMembershipRouterService)
	admitted := func(c echo.Context, run func(write *restWrite) (int, any, error)) error {
		write, release, err := admitRESTWrite(c, memberships.operations, memberships.audit)
		if write == nil {
			return err
		}
		defer release()
		status, result, err := run(write)
		if err != nil {
			return memberships.failure(c, err)
		}
		if status == http.StatusNoContent {
			return c.NoContent(status)
		}
		return c.JSON(status, result)
	}
	h.register(&routerStockViewset{
		prefix: "subscription/memberships", name: "Subscription Membership",
		formatted: map[string]bool{"create": true, "retrieve": true, "destroy": true},
		list: func(c echo.Context, identity *domain.ResolvedIdentity, _ string) error {
			result, err := memberships.memberships.List(c.Request().Context(), *identity, memberships.request(c))
			if err != nil {
				return memberships.failure(c, err)
			}
			return c.JSON(http.StatusOK, result)
		},
		create: func(c echo.Context, identity *domain.ResolvedIdentity, _ string) error {
			return admitted(c, func(write *restWrite) (int, any, error) {
				item, err := stock.Create(write.Context, *identity, memberships.request(c), write.Body, write.Form, write.Audit)
				return http.StatusCreated, item, err
			})
		},
		retrieve: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) error {
			item, err := stock.Retrieve(c.Request().Context(), *identity, pk, memberships.request(c))
			if err != nil {
				return memberships.failure(c, err)
			}
			return c.JSON(http.StatusOK, item)
		},
		update: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) error {
			return admitted(c, func(write *restWrite) (int, any, error) {
				item, err := memberships.memberships.Update(write.Context, *identity, pk, memberships.request(c), write.Body, write.Form, write.Audit)
				return http.StatusOK, item, err
			})
		},
		destroy: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) error {
			return memberships.remove(c, *identity, func(ctx context.Context, identity domain.ResolvedIdentity, audit domain.LoginAuditContext) error {
				return memberships.memberships.Delete(ctx, identity, pk, memberships.request(c), audit)
			})
		},
		postFields: func(echo.Context, *domain.ResolvedIdentity) (map[string]any, error) {
			return subscriptionMembershipAddActions["POST"].(map[string]any), nil
		},
		putFields: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) (map[string]any, error) {
			_, err := stock.Retrieve(c.Request().Context(), *identity, pk, memberships.request(c))
			if errors.Is(err, domain.ErrSubscriptionMembershipNotFound) || errors.Is(err, domain.ErrSubscriptionMembershipMalformed) {
				return nil, nil
			}
			if err != nil {
				return nil, err
			}
			return subscriptionMembershipAddActions["POST"].(map[string]any), nil
		},
	})
}
