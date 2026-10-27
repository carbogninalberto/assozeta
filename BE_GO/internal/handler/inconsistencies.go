package handler

import (
	"context"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type InconsistencyService interface {
	Check(context.Context, domain.ResolvedIdentity, string, domain.LoginAuditContext) (dto.InconsistencyReport, error)
}

type InconsistenciesHandler struct {
	checks     InconsistencyService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewInconsistenciesHandler(checks InconsistencyService, actors *service.ActorAuthentication, identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *InconsistenciesHandler {
	return &InconsistenciesHandler{checks: checks, actors: actors, identities: identities, audit: audit, operations: operations}
}

// Check is a GET that can write: get_main_tutor promotes and saves a relation.
func (h *InconsistenciesHandler) Check(c echo.Context) error {
	identity, err := messageReadsIdentity(c, h.actors, h.identities, http.MethodGet, "check_inconsistencies")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, "Check Inconsistencies")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
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
	report, err := h.checks.Check(ctx, *identity, request.Header.Get("X-Group-Id"), audit)
	if err != nil {
		return restError(c, err)
	}
	return c.JSON(200, report)
}
