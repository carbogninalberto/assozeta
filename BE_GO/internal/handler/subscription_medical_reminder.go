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

type SubscriptionMedicalReminderService interface {
	ParseUUID(raw string) (string, error)
	Send(ctx context.Context, identity domain.ResolvedIdentity, id, group string, audit domain.LoginAuditContext) error
}

// SubscriptionMedicalReminderHandler serves
// subscription/<uid>/medical-certificate/send-email-reminder (@api_view(['POST'])),
// whose success path publishes send_email_template. The view never reads request.data.
type SubscriptionMedicalReminderHandler struct {
	reminders  SubscriptionMedicalReminderService
	audit      LoginAuditReader
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	operations *paymentcategorylifecycle.Lifecycle
}

func NewSubscriptionMedicalReminderHandler(reminders SubscriptionMedicalReminderService, actors *service.ActorAuthentication,
	identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *SubscriptionMedicalReminderHandler {
	return &SubscriptionMedicalReminderHandler{reminders: reminders, audit: audit, actors: actors, identities: identities, operations: operations}
}

func (h *SubscriptionMedicalReminderHandler) Send(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "subscription_medical_certificate_send_email_reminder")
	if identity == nil {
		return err
	}
	if done, err := dispatchMedical(c, http.MethodPost, "Subscription Medical Certificate Send Email Reminder"); done {
		return err
	}
	request := c.Request()
	id, err := h.reminders.ParseUUID(c.Param("uid"))
	if err != nil {
		return messageReadsError(c, err)
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	login, err := h.audit(request)
	if err != nil {
		return messageReadsError(c, err)
	}
	if err = h.reminders.Send(ctx, *identity, id, request.Header.Get("X-Group-Id"), login); err != nil {
		if errors.Is(err, domain.ErrSubscriptionMedicalNotFound) {
			return c.JSON(http.StatusNotFound, map[string]string{"exception": "Subscription not found"})
		}
		return messageReadsError(c, err)
	}
	return c.JSON(http.StatusOK, map[string]string{"msg": "Email reminder sent"})
}
