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

type CommunicationSendEmailService interface {
	Send(context.Context, domain.ResolvedIdentity, any, bool, domain.LoginAuditContext) (service.SendEmailResult, error)
}

// CommunicationSendEmailHandler serves configuration_send_email (POST).
type CommunicationSendEmailHandler struct {
	send       CommunicationSendEmailService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewCommunicationSendEmailHandler(send CommunicationSendEmailService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *CommunicationSendEmailHandler {
	return &CommunicationSendEmailHandler{send: send, actors: actors, identities: identities, audit: audit, operations: operations}
}

func (h *CommunicationSendEmailHandler) Send(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "configuration_send_email")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, "Configuration Send Email")
	}
	if request.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	result, err := h.send.Send(write.Context, *identity, write.Body, write.Form, write.Audit)
	var invalid *dto.SendEmailInvalid
	switch {
	case err == nil:
		return c.JSON(200, result)
	case errors.As(err, &invalid):
		if invalid.List != "" {
			return c.JSON(400, []string{invalid.List})
		}
		fields := map[string]any{}
		for name, codes := range invalid.Fields {
			fields[name] = codes
		}
		return c.JSON(400, paymentCategoryValidationMessages(request, fields))
	case errors.Is(err, domain.ErrEmailLogsNotAssociation):
		return restError(c, &domain.PermissionDenied{Detail: err.Error()})
	case errors.Is(err, domain.ErrSendEmailMessageNotFound):
		return c.JSON(404, map[string]string{"msg": "message not found."})
	}
	if errors.Is(err, domain.ErrSendEmailNotPorted) {
		c.Logger().Error(err)
	}
	return restError(c, err)
}
