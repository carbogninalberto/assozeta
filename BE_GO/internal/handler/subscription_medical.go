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

type SubscriptionMedicalService interface {
	ParseUUID(raw string) (string, error)
	List(ctx context.Context, identity domain.ResolvedIdentity, raw, group string) (domain.RequestObject, error)
	Delete(ctx context.Context, identity domain.ResolvedIdentity, subscription, appointment string, audit domain.LoginAuditContext) error
	Edit(ctx context.Context, identity domain.ResolvedIdentity, id, group string, body any, audit domain.LoginAuditContext) error
	ExpirationTarget(ctx context.Context, identity domain.ResolvedIdentity, id, group string) error
	SetExpiration(ctx context.Context, identity domain.ResolvedIdentity, id, group string, body any, audit domain.LoginAuditContext) (string, error)
}

type SubscriptionMedicalHandler struct {
	medical    SubscriptionMedicalService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewSubscriptionMedicalHandler(medical SubscriptionMedicalService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *SubscriptionMedicalHandler {
	return &SubscriptionMedicalHandler{medical: medical, actors: actors, identities: identities, audit: audit, operations: operations}
}

// failure maps the views' responses; anything else is the 500 page.
func (h *SubscriptionMedicalHandler) failure(c echo.Context, err error) error {
	var invalid *service.PaymentInfoUUIDError
	var denied *domain.PermissionDenied
	switch {
	case errors.As(err, &invalid):
		return c.JSON(400, []string{invalid.Detail}) // is_valid_uuid
	case errors.As(err, &denied):
		return restError(c, denied)
	case errors.Is(err, domain.ErrSubscriptionMedicalNoSubscription):
		return c.JSON(404, map[string]string{"data": "no subscription found."})
	case errors.Is(err, domain.ErrSubscriptionMedicalNoAppointment):
		return c.JSON(404, map[string]string{"data": "no medical appointment found."})
	case errors.Is(err, domain.ErrSubscriptionMedicalNotFound):
		return c.JSON(404, map[string]string{"exception": "Subscription not found"})
	case errors.Is(err, domain.ErrSubscriptionMedicalNoCertificate):
		return c.JSON(404, map[string]string{"exception": "Medical certificate not found"})
	}
	return messageReadsError(c, err)
}

// dispatch answers OPTIONS and the wrong method (after identity and the registry).
func dispatchMedical(c echo.Context, method, name string) (bool, error) {
	request := c.Request()
	if request.Method == http.MethodOptions {
		return true, supplierOptions(c, name, "")
	}
	if request.Method != method {
		return true, restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	return false, nil
}

// List follows subscription_medical_appointments_list.
func (h *SubscriptionMedicalHandler) List(c echo.Context) error {
	identity, err := messageReadsIdentity(c, h.actors, h.identities, http.MethodGet, "subscription_medical_appointments_list")
	if identity == nil {
		return err
	}
	if done, err := dispatchMedical(c, http.MethodGet, "Subscription Medical Appointments List"); done {
		return err
	}
	request := c.Request()
	body, err := h.medical.List(request.Context(), *identity, c.Param("uid"), request.Header.Get("X-Group-Id"))
	if err != nil {
		return h.failure(c, err)
	}
	return c.Blob(http.StatusOK, echo.MIMEApplicationJSON, dto.PythonJSONDumpsCompact(body))
}

// Delete follows subscription_medical_appointments_delete; the view never reads request.data.
func (h *SubscriptionMedicalHandler) Delete(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodDelete, "subscription_medical_appointments_delete")
	if identity == nil {
		return err
	}
	if done, err := dispatchMedical(c, http.MethodDelete, "Subscription Medical Appointments Delete"); done {
		return err
	}
	request := c.Request()
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	auditContext, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	if err = h.medical.Delete(ctx, *identity, c.Param("uid"), c.Param("medical_appointments_id"), auditContext); err != nil {
		return h.failure(c, err)
	}
	return c.JSON(http.StatusOK, map[string]string{"data": "medical appointment created."})
}

// Edit follows subscription_medical_certificate_edit: is_valid_uuid, then
// request.data, then the lookup.
func (h *SubscriptionMedicalHandler) Edit(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "subscription_medical_certificate_edit")
	if identity == nil {
		return err
	}
	if done, err := dispatchMedical(c, http.MethodPost, "Subscription Medical Certificate Edit"); done {
		return err
	}
	id, err := h.medical.ParseUUID(c.Param("uid"))
	if err != nil {
		return h.failure(c, err)
	}
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	if err = h.medical.Edit(write.Context, *identity, id, c.Request().Header.Get("X-Group-Id"), write.Body, write.Audit); err != nil {
		return h.failure(c, err)
	}
	return c.JSON(http.StatusOK, map[string]string{"msg": "Medical certificate updated"})
}

// SetExpiration follows subscription_medical_certificate_set_certificate_expiration:
// is_valid_uuid, the lookup (404), then request.data.
func (h *SubscriptionMedicalHandler) SetExpiration(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost,
		"subscription_medical_certificate_set_certificate_expiration")
	if identity == nil {
		return err
	}
	if done, err := dispatchMedical(c, http.MethodPost, "Subscription Medical Certificate Set Certificate Expiration"); done {
		return err
	}
	request := c.Request()
	id, err := h.medical.ParseUUID(c.Param("uid"))
	if err != nil {
		return h.failure(c, err)
	}
	group := request.Header.Get("X-Group-Id")
	if err = h.medical.ExpirationTarget(request.Context(), *identity, id, group); err != nil {
		return h.failure(c, err)
	}
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	message, err := h.medical.SetExpiration(write.Context, *identity, id, group, write.Body, write.Audit)
	if err != nil {
		return h.failure(c, err)
	}
	return c.JSON(http.StatusOK, map[string]string{"msg": message})
}
