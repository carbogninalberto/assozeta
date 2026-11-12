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

type EmailLogsService interface {
	List(context.Context, domain.ResolvedIdentity) (dto.EmailLogsList, error)
}

type EmailLogsHandler struct {
	logs       EmailLogsService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewEmailLogsHandler(logs EmailLogsService, actors *service.ActorAuthentication, identities *service.IdentityResolver) *EmailLogsHandler {
	return &EmailLogsHandler{logs: logs, actors: actors, identities: identities}
}

// List is IsAuthenticated only: OPTIONS never evaluates sport_association.
func (h *EmailLogsHandler) List(c echo.Context) error {
	identity, err := messageReadsIdentity(c, h.actors, h.identities, http.MethodGet, "communication_email_logs_list")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, "Communication Email Logs List")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	list, err := h.logs.List(request.Context(), *identity)
	if errors.Is(err, domain.ErrEmailLogsNotAssociation) {
		return restError(c, &domain.PermissionDenied{Detail: err.Error()})
	}
	if err != nil {
		return restError(c, err)
	}
	return c.JSON(200, list)
}
