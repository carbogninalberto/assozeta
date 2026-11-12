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

type CalendarExportService interface {
	Export(ctx context.Context, identity domain.ResolvedIdentity, courseID *string, group string) ([]byte, error)
}

type CommunicationsPostService interface {
	Post(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) (any, error)
}

type SubscriptionCardService interface {
	Read(ctx context.Context, identity domain.ResolvedIdentity, raw string, token *string, group string, audit domain.LoginAuditContext) (any, error)
	Mint(ctx context.Context, identity domain.ResolvedIdentity, raw, group string, audit domain.LoginAuditContext) (any, error)
}

// MiscRoutesHandler serves calendar/events/export (attendee_views.py),
// communications/send/post (communications/views.py) and
// subscription/<uid>/card (subscriptions_views.py).
type MiscRoutesHandler struct {
	calendar   CalendarExportService
	post       CommunicationsPostService
	card       SubscriptionCardService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewMiscRoutesHandler(calendar CalendarExportService, post CommunicationsPostService, card SubscriptionCardService,
	actors *service.ActorAuthentication, identities *service.IdentityResolver, audit LoginAuditReader,
	operations *paymentcategorylifecycle.Lifecycle) *MiscRoutesHandler {
	return &MiscRoutesHandler{calendar: calendar, post: post, card: card, actors: actors, identities: identities, audit: audit,
		operations: operations}
}

// miscRoutesRespond maps the views' answers; anything else is the 500 page.
func miscRoutesRespond(c echo.Context, body any, err error) error {
	var invalid *service.PaymentInfoUUIDError
	var answer *domain.MiscRoutesAnswer
	switch {
	case err == nil:
		return c.Blob(http.StatusOK, echo.MIMEApplicationJSON, dto.PythonJSONDumpsCompact(body))
	case errors.As(err, &invalid):
		return c.JSON(http.StatusBadRequest, []string{invalid.Detail}) // is_valid_uuid
	case errors.As(err, &answer):
		return c.Blob(answer.Status, echo.MIMEApplicationJSON, dto.PythonJSONDumpsCompact(answer.Body))
	}
	return restError(c, err)
}

// CalendarExport is full_events_calendar_export: IsAuthenticated and
// IsAssociationUser before method dispatch.
func (h *MiscRoutesHandler) CalendarExport(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, "full_events_calendar_export")
	if identity == nil {
		return err
	}
	if denied, err := courseCalendarAssociationUser(c, identity); denied {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Full Events Calendar Export", "API endpoint to get the attendance registry for the current course")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	course := courseCalendarQueryValue(parseReferenceForm(request.URL.RawQuery), "course_id")
	body, err := h.calendar.Export(request.Context(), *identity, course, request.Header.Get("X-Group-Id"))
	if err != nil {
		return miscRoutesRespond(c, nil, err)
	}
	c.Response().Header().Set("Content-Disposition", `attachment; filename="events.ics"`)
	return c.Blob(http.StatusOK, "text/calendar", body)
}

// Post is configuration_send_post.
func (h *MiscRoutesHandler) Post(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "configuration_send_post")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Configuration Send Post", "")
	}
	if request.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	body, err := h.post.Post(write.Context, *identity, write.Body, write.Audit)
	return miscRoutesRespond(c, body, err)
}

// Card is subscription_card: no permission classes (anonymous requests are
// served); the registry keys are method-specific.
func (h *MiscRoutesHandler) Card(c echo.Context) error {
	c.Response().Header().Set("Allow", "GET, POST, OPTIONS")
	request := c.Request()
	identity, err := messageMutationsIdentityFor(c, h.actors, h.identities, request.Method, "subscription_card", true)
	if identity == nil {
		return err
	}
	switch request.Method {
	case http.MethodOptions:
		return supplierOptions(c, "Subscription Card", "")
	case http.MethodGet:
		write, release, err := admitRESTWrite(c, h.operations, h.audit)
		if write == nil {
			return err
		}
		defer release()
		token := courseCalendarQueryValue(parseReferenceForm(request.URL.RawQuery), "token")
		body, err := h.card.Read(write.Context, *identity, subscriptionReadsUID(c), token, request.Header.Get("X-Group-Id"), write.Audit)
		return miscRoutesRespond(c, body, err)
	case http.MethodPost:
		write, release, err := admitRESTWrite(c, h.operations, h.audit)
		if write == nil {
			return err
		}
		defer release()
		body, err := h.card.Mint(write.Context, *identity, subscriptionReadsUID(c), request.Header.Get("X-Group-Id"), write.Audit)
		return miscRoutesRespond(c, body, err)
	}
	return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
}
