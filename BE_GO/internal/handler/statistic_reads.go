package handler

import (
	"context"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type StatisticReadsService interface {
	Dashboard(ctx context.Context, identity domain.ResolvedIdentity, widget any, present bool, group *string) (service.StatisticResult, error)
	Report(ctx context.Context, identity domain.ResolvedIdentity, currentDate any, present bool, group *string) (service.StatisticResult, error)
	Athlete(ctx context.Context, identity domain.ResolvedIdentity, group *string) (service.StatisticResult, error)
}

// StatisticReadsHandler serves statistic/dashboard, statistic/report and
// statistic/athlete-dashboard (all @api_view(['GET'])).
type StatisticReadsHandler struct {
	statistics StatisticReadsService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewStatisticReadsHandler(statistics StatisticReadsService, actors *service.ActorAuthentication, identities *service.IdentityResolver) *StatisticReadsHandler {
	return &StatisticReadsHandler{statistics: statistics, actors: actors, identities: identities}
}

func (h *StatisticReadsHandler) admit(c echo.Context, operation, name string, association bool) (*domain.ResolvedIdentity, bool, error) {
	identity, err := messageReadsIdentity(c, h.actors, h.identities, http.MethodGet, operation)
	if identity == nil {
		return nil, false, err
	}
	request := c.Request()
	// IsAssociationUser runs with the other permissions, before method dispatch.
	if association && identity.ScopeUser.Role != domain.RoleAssociation && identity.ScopeUser.Role != domain.RoleCollaborator {
		return nil, false, restError(c, &domain.PermissionDenied{Detail: messageReadsPermissionDetail(request)})
	}
	if request.Method == http.MethodOptions {
		return nil, false, messageReadsOptions(c, name)
	}
	if request.Method != http.MethodGet {
		return nil, false, restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	return identity, true, nil
}

func (h *StatisticReadsHandler) respond(c echo.Context, result service.StatisticResult, err error) error {
	switch {
	case errors.Is(err, service.ErrStatisticAthleteFailure):
		return c.JSON(500, map[string]string{"error": "An error occurred while loading the dashboard"})
	case err != nil:
		// Unhandled exceptions and the refused (not ported) inputs.
		return restError(c, err)
	}
	return c.JSON(result.Status, result.Body)
}

func (h *StatisticReadsHandler) Dashboard(c echo.Context) error {
	identity, ok, err := h.admit(c, "statistic_dashboard", "Statistic Dashboard", false)
	if !ok {
		return err
	}
	request := c.Request()
	widget, present := parseReferenceForm(request.URL.RawQuery).Lookup("widget")
	result, err := h.statistics.Dashboard(request.Context(), *identity, widget, present, service.StatisticGroup(request.Header.Get("X-Group-Id")))
	return h.respond(c, result, err)
}

func (h *StatisticReadsHandler) Report(c echo.Context) error {
	identity, ok, err := h.admit(c, "statistic_report", "Statistic Report", true)
	if !ok {
		return err
	}
	request := c.Request()
	date, present := parseReferenceForm(request.URL.RawQuery).Lookup("currentDate")
	result, err := h.statistics.Report(request.Context(), *identity, date, present, service.StatisticGroup(request.Header.Get("X-Group-Id")))
	return h.respond(c, result, err)
}

func (h *StatisticReadsHandler) Athlete(c echo.Context) error {
	identity, ok, err := h.admit(c, "statistic_athlete_dashboard", "Statistic Athlete Dashboard", false)
	if !ok {
		return err
	}
	request := c.Request()
	result, err := h.statistics.Athlete(request.Context(), *identity, service.StatisticGroup(request.Header.Get("X-Group-Id")))
	return h.respond(c, result, err)
}
