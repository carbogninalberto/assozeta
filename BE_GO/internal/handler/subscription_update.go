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

type SubscriptionUpdateService interface {
	ParseUUID(raw string) (string, error)
	Target(ctx context.Context, identity domain.ResolvedIdentity, id, group string) error
	Update(ctx context.Context, identity domain.ResolvedIdentity, id, group string, body any, form bool, authorization *string,
		audit domain.LoginAuditContext) error
}

// SubscriptionUpdateHandler serves subscription/<uid>/update
// (@api_view(['PATCH'])), whose success path publishes print_document_subscription.
type SubscriptionUpdateHandler struct {
	updates    SubscriptionUpdateService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewSubscriptionUpdateHandler(updates SubscriptionUpdateService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *SubscriptionUpdateHandler {
	return &SubscriptionUpdateHandler{updates: updates, actors: actors, identities: identities, audit: audit, operations: operations}
}

func subscriptionUpdateFailure(c echo.Context, err error) error {
	var invalid *service.SubscriptionUpdateInvalid
	switch {
	case errors.Is(err, domain.ErrSubscriptionUpdateNotFound):
		return restError(c, &domain.PermissionDenied{Detail: "Subscription not found."})
	case errors.Is(err, domain.ErrSubscriptionUpdateDenied):
		return restError(c, &domain.PermissionDenied{Detail: "User not allowed."})
	case errors.As(err, &invalid):
		return c.JSON(http.StatusBadRequest, []string{invalid.Error()})
	}
	return messageReadsError(c, err)
}

// requestAuthorization is request.headers.get('authorization') (None when absent).
func requestAuthorization(request *http.Request) *string {
	if _, present := request.Header["Authorization"]; !present {
		return nil
	}
	value := request.Header.Get("Authorization")
	return &value
}

// Update follows subscription_update: is_valid_uuid, the lookups, then
// request.data, the conversions, the save and the publish.
func (h *SubscriptionUpdateHandler) Update(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPatch, "subscription_update")
	if identity == nil {
		return err
	}
	if done, err := dispatchMedical(c, http.MethodPatch, "Subscription Update"); done {
		return err
	}
	request := c.Request()
	id, err := h.updates.ParseUUID(c.Param("uid"))
	if err != nil {
		return subscriptionUpdateFailure(c, err)
	}
	group := request.Header.Get("X-Group-Id")
	if err = h.updates.Target(request.Context(), *identity, id, group); err != nil {
		return subscriptionUpdateFailure(c, err)
	}
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	if err = h.updates.Update(write.Context, *identity, id, group, write.Body, write.Form, requestAuthorization(request), write.Audit); err != nil {
		return subscriptionUpdateFailure(c, err)
	}
	return c.JSON(http.StatusOK, map[string]any{"data": map[string]string{"msg": "subscription updated."}})
}
