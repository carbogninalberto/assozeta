package handler

import (
	"context"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type PaymentStateService interface {
	Approve(ctx context.Context, identity domain.ResolvedIdentity, request service.PaymentStateRequest, body any) (map[string]any, error)
	Cancel(ctx context.Context, identity domain.ResolvedIdentity, request service.PaymentStateRequest) (map[string]any, error)
	Delete(ctx context.Context, identity domain.ResolvedIdentity, request service.PaymentStateRequest) (map[string]any, error)
	Mark(ctx context.Context, identity domain.ResolvedIdentity, request service.PaymentStateRequest, body any, column string) error
}

// PaymentStatesHandler serves the @api_view function views payment_approve,
// payment_cancel, payment_delete, payment_bulk_archive and payment_bulk_delete.
type PaymentStatesHandler struct {
	payments   PaymentStateService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewPaymentStatesHandler(payments PaymentStateService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *PaymentStatesHandler {
	return &PaymentStatesHandler{payments: payments, actors: actors, identities: identities, audit: audit, operations: operations}
}

func (h *PaymentStatesHandler) failure(c echo.Context, err error) error {
	var denied *domain.PermissionDenied
	switch {
	case errors.As(err, &denied):
		return restError(c, err)
	case errors.Is(err, domain.ErrPaymentStateNotFound):
		return c.JSON(404, map[string]string{"msg": "Payment not found."})
	case errors.Is(err, domain.ErrPaymentStateNotPaid):
		return c.JSON(400, map[string]string{"error": "Payment not paid."})
	}
	var invalid *dto.MessageReadUUIDError
	if errors.As(err, &invalid) {
		return messageReadsError(c, err)
	}
	// Every other failure (including refused, not ported paths) is the 500 page.
	return restError(c, domain.ErrPaymentStateServer)
}

// entry resolves the identity and answers OPTIONS and other methods; nil
// identity means the response was written.
func (h *PaymentStatesHandler) entry(c echo.Context, verb, operation, name, description string) (*domain.ResolvedIdentity, error) {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, verb, operation)
	if identity == nil {
		return nil, err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return nil, supplierOptions(c, name, description)
	case verb:
		return identity, nil
	}
	return nil, restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
}

// admitted runs the write admission without reading the body (the view never reads it).
func (h *PaymentStatesHandler) admitted(c echo.Context, run func(ctx context.Context, audit domain.LoginAuditContext) error) error {
	request := c.Request()
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
	return run(ctx, audit)
}

func (h *PaymentStatesHandler) request(c echo.Context, audit domain.LoginAuditContext) service.PaymentStateRequest {
	return service.PaymentStateRequest{ID: c.Param("uid"), GroupHeader: c.Request().Header.Get("X-Group-Id"), Audit: audit,
		Authorization: requestAuthorization(c.Request())}
}

func (h *PaymentStatesHandler) Approve(c echo.Context) error {
	identity, err := h.entry(c, http.MethodPost, "payment_approve", "Payment Approve", "")
	if identity == nil {
		return err
	}
	// is_valid_uuid runs before request.data is parsed.
	if _, err := service.PaymentStateUUID(c.Param("uid")); err != nil {
		return h.failure(c, err)
	}
	admitted, release, err := admitRESTWrite(c, h.operations, h.audit)
	if admitted == nil {
		return err
	}
	defer release()
	result, err := h.payments.Approve(admitted.Context, *identity, h.request(c, admitted.Audit), admitted.Body)
	if err != nil {
		return h.failure(c, err)
	}
	return c.JSON(http.StatusOK, result)
}

func (h *PaymentStatesHandler) Cancel(c echo.Context) error {
	identity, err := h.entry(c, http.MethodPost, "payment_cancel", "Payment Cancel", "")
	if identity == nil {
		return err
	}
	return h.admitted(c, func(ctx context.Context, audit domain.LoginAuditContext) error {
		result, err := h.payments.Cancel(ctx, *identity, h.request(c, audit))
		if err != nil {
			return h.failure(c, err)
		}
		return c.JSON(http.StatusOK, result)
	})
}

func (h *PaymentStatesHandler) Delete(c echo.Context) error {
	identity, err := h.entry(c, http.MethodDelete, "payment_delete", "Payment Delete", "API endpoint to add a new payment")
	if identity == nil {
		return err
	}
	return h.admitted(c, func(ctx context.Context, audit domain.LoginAuditContext) error {
		result, err := h.payments.Delete(ctx, *identity, h.request(c, audit))
		if err != nil {
			return h.failure(c, err)
		}
		return c.JSON(http.StatusOK, result)
	})
}

func (h *PaymentStatesHandler) mark(c echo.Context, operation, name, column, message string) error {
	identity, err := h.entry(c, http.MethodPost, operation, name, "")
	if identity == nil {
		return err
	}
	admitted, release, err := admitRESTWrite(c, h.operations, h.audit)
	if admitted == nil {
		return err
	}
	defer release()
	if err := h.payments.Mark(admitted.Context, *identity, h.request(c, admitted.Audit), admitted.Body, column); err != nil {
		return h.failure(c, err)
	}
	return c.JSON(http.StatusOK, map[string]string{"message": message})
}

func (h *PaymentStatesHandler) BulkArchive(c echo.Context) error {
	return h.mark(c, "payment_bulk_archive", "Payment Bulk Archive", "archived", "All payments archived")
}

func (h *PaymentStatesHandler) BulkDelete(c echo.Context) error {
	return h.mark(c, "payment_bulk_delete", "Payment Bulk Delete", "deleted", "All payments deleted")
}
