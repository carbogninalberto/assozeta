package handler

import (
	"context"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type AuditSummaryService interface {
	Stats(context.Context, domain.ResolvedIdentity) (map[string]any, error)
	Models(context.Context, domain.ResolvedIdentity) (map[string]any, error)
}

type AuditSummaryHandler struct {
	summaries  AuditSummaryService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewAuditSummaryHandler(summaries AuditSummaryService, actors *service.ActorAuthentication, identities *service.IdentityResolver) *AuditSummaryHandler {
	return &AuditSummaryHandler{summaries: summaries, actors: actors, identities: identities}
}

func (h *AuditSummaryHandler) serve(c echo.Context, operation, name, description string, read func(context.Context, domain.ResolvedIdentity) (map[string]any, error)) error {
	identity, err := messageReadsIdentity(c, h.actors, h.identities, http.MethodGet, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, name, description)
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	body, err := read(request.Context(), *identity)
	switch {
	case errors.Is(err, domain.ErrAssociationNotFound):
		return c.JSON(404, map[string]string{"error": "Sport association not found"})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, body)
}

func (h *AuditSummaryHandler) Stats(c echo.Context) error {
	return h.serve(c, "audit_log_stats", "Audit Log Stats", "API endpoint to get audit log statistics for the current sport association.", h.summaries.Stats)
}

func (h *AuditSummaryHandler) Models(c echo.Context) error {
	return h.serve(c, "audit_log_models", "Audit Log Models",
		"API endpoint to list available model types for filtering.\nReturns models that have audit logs for the current sport association.", h.summaries.Models)
}
