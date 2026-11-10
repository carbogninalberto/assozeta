package handler

import (
	"context"
	"errors"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// GoogleCalendarHandler serves google/check, google/calendar/config,
// google/oauth2callback, google/calendar/list and
// google/calendar/<course_id>/export (BE/application/views/google_views.py).
type GoogleCalendarHandler struct {
	google     *service.GoogleCalendar
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
	// secureCookie is SESSION_COOKIE_SECURE.
	secureCookie bool
	now          func() time.Time
}

func NewGoogleCalendarHandler(google *service.GoogleCalendar, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle, secureCookie bool, now func() time.Time) *GoogleCalendarHandler {
	return &GoogleCalendarHandler{google: google, actors: actors, identities: identities, audit: audit, operations: operations,
		secureCookie: secureCookie, now: now}
}

func googleHTMLPage(text string) string {
	return "\n            <html>\n                <head>\n                    <script type=\"text/javascript\">\n" +
		"                        window.close();\n                    </script>            \n                </head>\n" +
		"                <body>\n                    <p>" + text + "</p>\n                </body>\n            </html>\n        "
}

var (
	googleCancelledPage  = googleHTMLPage("Richiesta cancellata. Per favore chiudi la pagina.")
	googleAuthorizedPage = googleHTMLPage("Autorizzato. Per favore chiudi la pagina.")
)

// admit tracks the request as a write (the views save rows) and reads the audit context.
func (h *GoogleCalendarHandler) admit(c echo.Context, run func(ctx context.Context, audit domain.LoginAuditContext) error) error {
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
	return run(ctx, audit)
}

func (h *GoogleCalendarHandler) failure(c echo.Context, err error) error {
	switch {
	case errors.Is(err, domain.ErrGoogleNoUser):
		return c.JSON(http.StatusBadRequest, map[string]string{"msg": err.Error()})
	}
	// Unhandled view exceptions and refused (not ported) inputs are the 500 page.
	if !errors.Is(err, domain.ErrGoogleServer) {
		return restError(c, err)
	}
	return restError(c, domain.ErrGoogleServer)
}

func methodNotAllowed(c echo.Context) error {
	return restError(c, &restInputError{Status: 405, Detail: restText(c.Request(), "method")})
}

// Check is google_check (GET, IsAuthenticated).
func (h *GoogleCalendarHandler) Check(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, "google_check")
	if identity == nil {
		return err
	}
	switch c.Request().Method {
	case http.MethodOptions:
		return supplierOptions(c, "Google Check", "")
	case http.MethodGet:
		body, err := h.google.Check(c.Request().Context(), *identity)
		if err != nil {
			return h.failure(c, err)
		}
		return c.JSON(http.StatusOK, body)
	}
	return methodNotAllowed(c)
}

// Config is google_calendar_config (GET, DELETE; IsAuthenticated).
func (h *GoogleCalendarHandler) Config(c echo.Context) error {
	c.Response().Header().Set("Allow", "GET, DELETE, OPTIONS")
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, "google_calendar_config")
	if identity == nil {
		return err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return supplierOptions(c, "Google Calendar Config", "")
	case http.MethodGet:
		sessionKey, ok := googleCalendarSessionCookie(request)
		if !ok {
			return h.failure(c, domain.ErrGoogleServer)
		}
		return h.admit(c, func(ctx context.Context, audit domain.LoginAuditContext) error {
			answer, err := h.google.ConfigGet(ctx, *identity, audit, sessionKey)
			if err != nil {
				return h.failure(c, err)
			}
			// SessionMiddleware: the new session's cookie (SESSION_COOKIE_AGE, HttpOnly, Lax).
			cookie := "sessionid=" + answer.SessionKey + "; expires=" + h.now().Add(1209600*time.Second).UTC().Format(http.TimeFormat) +
				"; HttpOnly; Max-Age=1209600; Path=/; SameSite=Lax"
			if h.secureCookie {
				cookie += "; Secure"
			}
			c.Response().Header().Add("Set-Cookie", cookie)
			c.Response().Header().Add("Vary", "Cookie")
			return c.JSON(http.StatusOK, answer.Body)
		})
	case http.MethodDelete:
		return h.admit(c, func(ctx context.Context, audit domain.LoginAuditContext) error {
			body, err := h.google.ConfigDelete(ctx, *identity, audit)
			if err != nil {
				return h.failure(c, err)
			}
			return c.JSON(http.StatusOK, body)
		})
	}
	return methodNotAllowed(c)
}

// Callback is google_oauth2callback (GET, AllowAny).
func (h *GoogleCalendarHandler) Callback(c echo.Context) error {
	identity, err := messageMutationsIdentityFor(c, h.actors, h.identities, http.MethodGet, "google_oauth2callback", true)
	if identity == nil {
		return err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return supplierOptions(c, "Google Oauth2Callback", "")
	case http.MethodGet:
		query := parseReferenceForm(request.URL.RawQuery)
		if _, cancelled := query.Lookup("error"); cancelled {
			return c.Blob(http.StatusOK, "text/html", []byte(googleCancelledPage))
		}
		return h.admit(c, func(ctx context.Context, audit domain.LoginAuditContext) error {
			if err := h.google.Callback(ctx, *identity, query, audit); err != nil {
				return h.failure(c, err)
			}
			return c.Blob(http.StatusOK, "text/html", []byte(googleAuthorizedPage))
		})
	}
	return methodNotAllowed(c)
}

// List is google_calendar_list (GET, IsAuthenticated).
func (h *GoogleCalendarHandler) List(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, "google_calendar_list")
	if identity == nil {
		return err
	}
	switch c.Request().Method {
	case http.MethodOptions:
		return supplierOptions(c, "Google Calendar List", "")
	case http.MethodGet:
		return h.admit(c, func(ctx context.Context, audit domain.LoginAuditContext) error {
			body, err := h.google.List(ctx, *identity, audit)
			if err != nil {
				return h.failure(c, err)
			}
			return c.Blob(http.StatusOK, echo.MIMEApplicationJSON, dto.PythonJSONDumpsCompact(body))
		})
	}
	return methodNotAllowed(c)
}

// Export is google_calendar_export_course (POST, IsAuthenticated).
func (h *GoogleCalendarHandler) Export(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "google_calendar_export_course")
	if identity == nil {
		return err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return supplierOptions(c, "Google Calendar Export Course", "")
	case http.MethodPost:
		course := c.Param("course_id")
		if request.URL.RawPath != "" {
			if course, err = url.PathUnescape(course); err != nil {
				return restError(c, err)
			}
		}
		return h.admit(c, func(ctx context.Context, audit domain.LoginAuditContext) error {
			result, err := h.google.Export(ctx, *identity, course, request.Header.Get("X-Group-Id"), audit)
			if err != nil {
				return h.failure(c, err)
			}
			return c.JSON(result.Status, result.Body)
		})
	}
	return methodNotAllowed(c)
}

// googleCalendarSessionCookie is SessionMiddleware's
// request.COOKIES.get('sessionid') (django.http.parse_cookie: chunks split on
// ';', the last sessionid wins). ok is false for what this port does not
// reproduce: several Cookie headers carrying a sessionid, and values that
// are not plain session-key characters (Python's strip and unquoting).
func googleCalendarSessionCookie(request *http.Request) (string, bool) {
	headers := request.Header.Values("Cookie")
	key, seen := "", 0
	for _, header := range headers {
		for _, chunk := range strings.Split(header, ";") {
			name, value, found := strings.Cut(chunk, "=")
			if !found {
				continue
			}
			if strings.TrimSpace(name) != "sessionid" {
				continue
			}
			seen++
			key = strings.Trim(value, " \t")
		}
	}
	if seen > 0 && len(headers) > 1 {
		return "", false
	}
	for _, r := range key {
		if !(r >= 'a' && r <= 'z' || r >= '0' && r <= '9') {
			return "", false
		}
	}
	return key, true
}
