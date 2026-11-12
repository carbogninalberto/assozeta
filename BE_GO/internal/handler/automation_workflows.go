package handler

import (
	"context"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type AutomationWorkflowReadService interface {
	Admit(context.Context, domain.ResolvedIdentity) error
	List(context.Context, domain.ResolvedIdentity) ([]dto.AutomationWorkflowRow, error)
	Details(context.Context, domain.ResolvedIdentity, string) (dto.AutomationWorkflowRow, error)
}

type AutomationWorkflowReadsHandler struct {
	reads      AutomationWorkflowReadService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewAutomationWorkflowReadsHandler(reads AutomationWorkflowReadService, actors *service.ActorAuthentication, identities *service.IdentityResolver) *AutomationWorkflowReadsHandler {
	return &AutomationWorkflowReadsHandler{reads: reads, actors: actors, identities: identities}
}

func (h *AutomationWorkflowReadsHandler) List(c echo.Context) error {
	return h.serve(c, "communication_workflows_list", "Communication Workflows List", func(identity domain.ResolvedIdentity) (any, error) {
		return h.reads.List(c.Request().Context(), identity)
	})
}

func (h *AutomationWorkflowReadsHandler) Details(c echo.Context) error {
	return h.serve(c, "communication_workflows_details", "Communication Workflows Details", func(identity domain.ResolvedIdentity) (any, error) {
		return h.reads.Details(c.Request().Context(), identity, c.Param("workflow_id"))
	})
}

func (h *AutomationWorkflowReadsHandler) serve(c echo.Context, operation, name string, read func(domain.ResolvedIdentity) (any, error)) error {
	identity, err := messageReadsIdentity(c, h.actors, h.identities, http.MethodGet, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	if err = h.reads.Admit(request.Context(), *identity); err != nil {
		return messageReadsError(c, err)
	}
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, name)
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	result, err := read(*identity)
	if errors.Is(err, domain.ErrAutomationWorkflowNotFound) {
		return c.JSON(404, map[string]string{"msg": "workflow not found."})
	}
	if err != nil {
		return messageReadsError(c, err)
	}
	return c.JSON(200, result)
}
