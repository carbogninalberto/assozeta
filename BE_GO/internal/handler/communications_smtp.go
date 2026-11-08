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

type CommunicationSMTPService interface {
	Info(context.Context, domain.ResolvedIdentity, domain.LoginAuditContext) (dto.CommunicationConfigurationInfo, error)
	Update(context.Context, domain.ResolvedIdentity, any, bool, domain.LoginAuditContext) error
	Verify(context.Context, domain.ResolvedIdentity) (bool, string, error)
}

// CommunicationSMTPHandler serves the IsAuthenticated function views
// configuration_smtp_info (GET, may create), _update (PATCH) and _verify (GET).
type CommunicationSMTPHandler struct {
	smtp       CommunicationSMTPService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewCommunicationSMTPHandler(smtp CommunicationSMTPService, actors *service.ActorAuthentication, identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *CommunicationSMTPHandler {
	return &CommunicationSMTPHandler{smtp: smtp, actors: actors, identities: identities, audit: audit, operations: operations}
}

// dispatch runs identity, OPTIONS and method dispatch; a false result means a
// response was written. OPTIONS never evaluates request.user.sport_association.
func (h *CommunicationSMTPHandler) dispatch(c echo.Context, method, operation, name string) (*domain.ResolvedIdentity, bool, error) {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, method, operation)
	if identity == nil {
		return nil, false, err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return nil, false, messageReadsOptions(c, name)
	}
	if request.Method != method {
		return nil, false, restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	return identity, true, nil
}

func communicationSMTPError(c echo.Context, err error) error {
	switch {
	case errors.Is(err, domain.ErrEmailLogsNotAssociation):
		return restError(c, &domain.PermissionDenied{Detail: err.Error()})
	case errors.Is(err, domain.ErrCommunicationConfigurationNotFound):
		return c.JSON(404, map[string]string{"msg": "info not found."})
	case errors.Is(err, dto.ErrCommunicationSMTPInvalid):
		return c.JSON(400, map[string]string{"msg": "info not valid."})
	}
	if errors.Is(err, domain.ErrSMTPVerifyNotPorted) {
		c.Logger().Error(err)
	}
	return restError(c, err)
}

func (h *CommunicationSMTPHandler) Info(c echo.Context) error {
	identity, ok, err := h.dispatch(c, http.MethodGet, "configuration_smtp_info", "Configuration Smtp Info")
	if !ok {
		return err
	}
	// A GET that may create the configuration: tracked like a write, without reading a body.
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
	info, err := h.smtp.Info(ctx, *identity, audit)
	if err != nil {
		return communicationSMTPError(c, err)
	}
	return c.JSON(200, info)
}

func (h *CommunicationSMTPHandler) Update(c echo.Context) error {
	identity, ok, err := h.dispatch(c, http.MethodPatch, "configuration_smtp_update", "Configuration Smtp Update")
	if !ok {
		return err
	}
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	if err = h.smtp.Update(write.Context, *identity, write.Body, write.Form, write.Audit); err != nil {
		return communicationSMTPError(c, err)
	}
	return c.JSON(200, map[string]string{"msg": "info updated."})
}

func (h *CommunicationSMTPHandler) Verify(c echo.Context) error {
	identity, ok, err := h.dispatch(c, http.MethodGet, "configuration_smtp_verify", "Configuration Smtp Verify")
	if !ok {
		return err
	}
	verified, message, err := h.smtp.Verify(c.Request().Context(), *identity)
	if err != nil {
		return communicationSMTPError(c, err)
	}
	return c.JSON(200, map[string]any{"verified": verified, "msg": message})
}
