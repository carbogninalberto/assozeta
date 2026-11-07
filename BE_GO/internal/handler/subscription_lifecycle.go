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

type SubscriptionLifecycleService interface {
	Reject(context.Context, domain.ResolvedIdentity, string, string, domain.LoginAuditContext) error
	Archive(context.Context, domain.ResolvedIdentity, string, string, domain.LoginAuditContext) error
	Delete(context.Context, domain.ResolvedIdentity, string, string, domain.LoginAuditContext) error
}

// SubscriptionLifecycleHandler serves subscription/<uid>/reject, archive and
// delete (@api_view(['POST'])). subscription/<uid>/approve is not ported: its
// success path dispatches a Celery task.
type SubscriptionLifecycleHandler struct {
	lifecycles SubscriptionLifecycleService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewSubscriptionLifecycleHandler(lifecycles SubscriptionLifecycleService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *SubscriptionLifecycleHandler {
	return &SubscriptionLifecycleHandler{lifecycles: lifecycles, actors: actors, identities: identities, audit: audit, operations: operations}
}

type subscriptionLifecycleCall func(context.Context, domain.ResolvedIdentity, string, string, domain.LoginAuditContext) error

func (h *SubscriptionLifecycleHandler) serve(c echo.Context, operation, name string, associationUser bool, call subscriptionLifecycleCall,
	render func(echo.Context, error) error, message string) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	// IsAssociationUser runs with the other permissions, before method dispatch.
	if associationUser && identity.ScopeUser.Role != domain.RoleAssociation && identity.ScopeUser.Role != domain.RoleCollaborator {
		return restError(c, &domain.PermissionDenied{Detail: messageReadsPermissionDetail(request)})
	}
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, name)
	}
	if request.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	audit, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	if err = call(ctx, *identity, c.Param("uid"), request.Header.Get("X-Group-Id"), audit); err != nil {
		return render(c, err)
	}
	return c.JSON(200, map[string]any{"data": map[string]string{"msg": message}})
}

func (h *SubscriptionLifecycleHandler) Reject(c echo.Context) error {
	return h.serve(c, "subscription_reject", "Subscription Reject", false, h.lifecycles.Reject, func(c echo.Context, err error) error {
		switch {
		case errors.Is(err, domain.ErrSubscriptionLifecycleNotFound):
			return c.JSON(404, map[string]string{"msg": "Subscription not found."})
		case errors.Is(err, service.ErrSubscriptionLifecycleForeign):
			return restError(c, &domain.PermissionDenied{Detail: "User not allowed."})
		case errors.Is(err, service.ErrSubscriptionLifecycleRejected):
			return restError(c, &domain.PermissionDenied{Detail: "User cannot change the status of the subscription."})
		}
		return messageReadsError(c, err)
	}, "subscription rejected.")
}

// subscriptionLifecycleCaught renders the archive/delete try block: a
// PermissionDenied as {'exception': detail}, a lookup ValidationError as its
// message list (500); any other exception cannot be rendered (500 page).
func subscriptionLifecycleCaught(c echo.Context, err error, missing any) error {
	var lookup *service.SubscriptionLifecycleLookupError
	switch {
	case errors.Is(err, domain.ErrSubscriptionLifecycleNotFound):
		return c.JSON(404, missing)
	case errors.Is(err, service.ErrSubscriptionLifecycleForeign):
		return c.JSON(403, map[string]string{"exception": "User not allowed."})
	case errors.As(err, &lookup):
		return c.JSON(500, map[string][]string{"exception": {lookup.Message}})
	}
	return messageReadsError(c, err)
}

func (h *SubscriptionLifecycleHandler) Archive(c echo.Context) error {
	return h.serve(c, "subscription_archive", "Subscription Archive", true, h.lifecycles.Archive, func(c echo.Context, err error) error {
		return subscriptionLifecycleCaught(c, err, map[string]string{"exception": "Subscription not found"})
	}, "subscription archival completed.")
}

func (h *SubscriptionLifecycleHandler) Delete(c echo.Context) error {
	return h.serve(c, "subscription_delete", "Subscription Delete", false, h.lifecycles.Delete, func(c echo.Context, err error) error {
		return subscriptionLifecycleCaught(c, err, map[string]string{"msg": "Subscription not found."})
	}, "subscription delete.")
}
