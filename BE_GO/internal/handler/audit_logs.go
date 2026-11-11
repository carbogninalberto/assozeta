package handler

import (
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type AuditLogHandler struct {
	logs       *service.AuditLogs
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewAuditLogHandler(logs *service.AuditLogs, actors *service.ActorAuthentication, identities *service.IdentityResolver) *AuditLogHandler {
	return &AuditLogHandler{logs: logs, actors: actors, identities: identities}
}

const auditLogListDescription = "API endpoint to list audit logs for the current sport association.\n\nQuery parameters:\n" +
	"- pagination[page]: Page number (default: 1)\n- pagination[perpage]: Items per page (default: 10)\n" +
	"- query[generalSearch]: Search in object_repr, actor name/email\n" +
	"- query[action]: Filter by action type (0=CREATE, 1=UPDATE, 2=DELETE)\n" +
	"- query[model]: Filter by model name(s), comma-separated\n- query[actor_id]: Filter by actor user ID\n" +
	"- query[object_id]: Filter by specific object ID (UUID) - shows all changes to that object\n" +
	"- query[date_from]: Filter from date (YYYY-MM-DD)\n- query[date_to]: Filter to date (YYYY-MM-DD)\n" +
	"- sort[field]: Sort field (timestamp, action, model)\n- sort[sort]: Sort direction (asc, desc)\n" +
	"- minimal: If \"true\", return minimal fields only (default: false)"

// serve runs DRF's identity, OPTIONS and method checks for an @api_view(['GET'])
// view outside the permission registry, then renders the body like DRF's
// strict JSONRenderer (a non-finite float is a 500).
func (h *AuditLogHandler) serve(c echo.Context, operation, name, description string, read func(domain.ResolvedIdentity) (any, error)) error {
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
	body, err := read(*identity)
	switch {
	case errors.Is(err, domain.ErrAssociationNotFound):
		return c.JSON(404, map[string]string{"error": "Sport association not found"})
	case errors.Is(err, service.ErrAuditLogNotFound):
		return c.JSON(404, map[string]string{"error": "Audit log not found"})
	case errors.Is(err, dto.ErrDRFInvalidPage):
		return c.JSON(404, map[string]string{"detail": "Invalid page."})
	case err != nil:
		return messageReadsError(c, err)
	case !dto.AuditResponseEncodable(body):
		return messageReadsError(c, dto.ErrAuditUnhandled)
	}
	return c.Blob(200, echo.MIMEApplicationJSON, dto.PythonJSONDumpsCompact(body))
}

func (h *AuditLogHandler) List(c echo.Context) error {
	request := c.Request()
	return h.serve(c, "audit_log_list", "Audit Log List", auditLogListDescription, func(identity domain.ResolvedIdentity) (any, error) {
		return h.logs.List(request.Context(), identity, parseReferenceForm(request.URL.RawQuery), request.Header.Get("X-Group-Id"))
	})
}

// Detail serves audit-logs/<int:log_id>/detail; a path segment the int
// converter ([0-9]+) rejects is an unmatched URL.
func (h *AuditLogHandler) Detail(c echo.Context) error {
	id := c.Param("id")
	if id == "" || len(id) != len([]rune(id)) || !auditDigits(id) {
		return echo.ErrNotFound
	}
	request := c.Request()
	return h.serve(c, "audit_log_detail", "Audit Log Detail", "API endpoint to get detailed information about a single audit log entry.",
		func(identity domain.ResolvedIdentity) (any, error) {
			return h.logs.Detail(request.Context(), identity, id, request.Header.Get("X-Group-Id"))
		})
}

func auditDigits(text string) bool {
	for i := 0; i < len(text); i++ {
		if text[i] < '0' || text[i] > '9' {
			return false
		}
	}
	return true
}
