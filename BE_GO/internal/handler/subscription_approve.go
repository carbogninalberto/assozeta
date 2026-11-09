package handler

import (
	"context"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type SubscriptionApproveService interface {
	Approve(context.Context, domain.ResolvedIdentity, string, string, domain.LoginAuditContext) error
}

// SubscriptionApproveHandler serves subscription/<uid>/approve
// (@api_view(['POST'])), whose success path publishes a Celery task.
type SubscriptionApproveHandler struct {
	shared    *SubscriptionLifecycleHandler
	approvals SubscriptionApproveService
}

func NewSubscriptionApproveHandler(approvals SubscriptionApproveService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *SubscriptionApproveHandler {
	return &SubscriptionApproveHandler{shared: NewSubscriptionLifecycleHandler(nil, actors, identities, audit, operations), approvals: approvals}
}

func (h *SubscriptionApproveHandler) Approve(c echo.Context) error {
	return h.shared.serve(c, "subscription_approve", "Subscription Approve", false, h.approvals.Approve, func(c echo.Context, err error) error {
		switch {
		case errors.Is(err, domain.ErrSubscriptionLifecycleNotFound):
			return c.JSON(404, map[string]string{"msg": "Subscription not found."})
		case errors.Is(err, service.ErrSubscriptionLifecycleForeign):
			return restError(c, &domain.PermissionDenied{Detail: "User not allowed."})
		case errors.Is(err, service.ErrSubscriptionApproveAccepted):
			return restError(c, &domain.PermissionDenied{Detail: "User cannot change the status of the subscription."})
		}
		return messageReadsError(c, err)
	}, "subscription approved.")
}
