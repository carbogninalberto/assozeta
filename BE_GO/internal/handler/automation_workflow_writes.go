package handler

import (
	"context"
	"errors"
	"mime"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type AutomationWorkflowWriteService interface {
	Admit(context.Context, domain.ResolvedIdentity) error
	Add(context.Context, domain.ResolvedIdentity, any, bool, domain.LoginAuditContext) (string, error)
	Delete(context.Context, domain.ResolvedIdentity, string, domain.LoginAuditContext) error
}

type AutomationWorkflowWritesHandler struct {
	writes     AutomationWorkflowWriteService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewAutomationWorkflowWritesHandler(writes AutomationWorkflowWriteService, actors *service.ActorAuthentication, identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *AutomationWorkflowWritesHandler {
	return &AutomationWorkflowWritesHandler{writes: writes, actors: actors, identities: identities, audit: audit, operations: operations}
}

func (h *AutomationWorkflowWritesHandler) Add(c echo.Context) error {
	return h.serve(c, http.MethodPost, "communication_workflows_add", "Communication Workflows Add")
}

func (h *AutomationWorkflowWritesHandler) Delete(c echo.Context) error {
	return h.serve(c, http.MethodDelete, "communication_workflows_delete", "Communication Workflows Delete")
}

func (h *AutomationWorkflowWritesHandler) serve(c echo.Context, method, operation, name string) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, method, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	if err = h.writes.Admit(request.Context(), *identity); err != nil {
		return messageReadsError(c, err)
	}
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, name)
	}
	if request.Method != method {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	var body any
	if method == http.MethodPost {
		if body, err = readMessageMutationsBody(c); err != nil {
			return restError(c, err)
		}
	}
	audit, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	operationContext, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	if method == http.MethodPost {
		media, _, _ := mime.ParseMediaType(request.Header.Get("Content-Type"))
		form := media == "application/x-www-form-urlencoded" || media == "multipart/form-data"
		id, err := h.writes.Add(operationContext, *identity, body, form, audit)
		if errors.Is(err, dto.ErrAutomationWorkflowInvalid) {
			return c.JSON(400, map[string]string{"msg": "workflow not valid."})
		}
		if err != nil {
			return messageReadsError(c, err)
		}
		return c.JSON(200, map[string]string{"msg": "workflow added.", "workflow_id": id})
	}
	if err = h.writes.Delete(operationContext, *identity, c.Param("workflow_id"), audit); err != nil {
		return messageReadsError(c, err)
	}
	return c.JSON(200, map[string]string{"msg": "workflow deleted."})
}
