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

type SubscriptionTransferService interface {
	Transfer(ctx context.Context, identity domain.ResolvedIdentity, request service.SubscriptionTransferRequest) (*service.SubscriptionTransferResult, error)
}

// SubscriptionTransferHandler serves subscription/<uid>/transfer
// (@api_view(['POST']), IsAuthenticated); see subscription-transfer.md.
type SubscriptionTransferHandler struct {
	transfers  SubscriptionTransferService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewSubscriptionTransferHandler(transfers SubscriptionTransferService, actors *service.ActorAuthentication,
	identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *SubscriptionTransferHandler {
	return &SubscriptionTransferHandler{transfers: transfers, actors: actors, identities: identities, audit: audit, operations: operations}
}

// Transfer admits the write like admitRESTWrite, but keeps a body parse error
// for the point where the view first reads request.data.
func (h *SubscriptionTransferHandler) Transfer(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "subscription_transfer")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Subscription Transfer", "")
	}
	if request.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	body, bodyErr := readMessageMutationsBody(c)
	request = c.Request() // the authenticated body read attaches the audit actor
	auditContext, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	result, err := h.transfers.Transfer(ctx, *identity, service.SubscriptionTransferRequest{UID: c.Param("uid"),
		Group: request.Header.Get("X-Group-Id"), Body: body, BodyError: bodyErr, Audit: auditContext,
		Language: djangoReadRouteLocale{}.Language(request)})
	var invalid *service.PaymentInfoUUIDError
	var denied *domain.PermissionDenied
	switch {
	case err == nil:
		return c.JSON(http.StatusCreated, result)
	case errors.As(err, &invalid):
		return c.JSON(http.StatusBadRequest, []string{invalid.Detail})
	case errors.Is(err, service.ErrSubscriptionTransferNotFound):
		return c.JSON(http.StatusNotFound, map[string]string{"msg": "Subscription not found."})
	case errors.Is(err, service.ErrSubscriptionTransferNotOwner):
		return c.JSON(http.StatusForbidden, map[string]string{"msg": "You are not the owner of the subscription."})
	case errors.Is(err, service.ErrSubscriptionTransferPending):
		return c.JSON(http.StatusBadRequest, map[string]string{"msg": "C'è già una richiesta di trasferimento in attesa di essere approvata."})
	case errors.Is(err, service.ErrSubscriptionTransferSelf):
		return c.JSON(http.StatusConflict, map[string]string{"msg": "You cannot transfer the subscription to yourself."})
	case errors.Is(err, service.ErrSubscriptionTransferNoRecipient):
		return c.NoContent(http.StatusBadRequest)
	case errors.Is(err, service.ErrSubscriptionTransferNoEmail):
		return c.JSON(http.StatusBadRequest, map[string]string{"msg": "Email is required."})
	case errors.As(err, &denied):
		return restError(c, denied)
	case err == bodyErr:
		return restError(c, err)
	}
	return messageReadsError(c, err)
}
