package handler

import (
	"errors"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/labstack/echo/v4"
)

// ChatTestSession reads the request's cache-session audit context
// (middleware.SessionAuditContext): ActorID is request.user when the session
// authenticates an active user.
type ChatTestSession func(*http.Request) (domain.LoginAuditContext, error)

// ChatTestHandler is application.chat.views.chat_test: @login_required around
// render(request, 'chat/test.html'). The template does not exist in BE, so an
// authenticated request ends in TemplateDoesNotExist (the stable 500 page);
// anyone else is redirected to settings.LOGIN_URL with next.
type ChatTestHandler struct {
	session          ChatTestSession
	csrfCookieSecure bool
}

func NewChatTestHandler(session ChatTestSession, csrfCookieSecure bool) *ChatTestHandler {
	return &ChatTestHandler{session: session, csrfCookieSecure: csrfCookieSecure}
}

var errChatTestTemplate = errors.New("TemplateDoesNotExist: chat/test.html")

// chatTestLoginURL is the default settings.LOGIN_URL (core.settings sets none).
const chatTestLoginURL = "/accounts/login/"

// ReissueMalformedCSRFCookie is CsrfViewMiddleware.process_request: a csrftoken cookie that is not a well-formed
// secret is replaced by a new one on the response, whatever the method.
func (h *ChatTestHandler) ReissueMalformedCSRFCookie(next echo.HandlerFunc) echo.HandlerFunc {
	return func(c echo.Context) error {
		if value, found := djangoRequestCookie(c.Request(), "csrftoken"); found && !csrfWellFormed(value) {
			cookie := fmt.Sprintf("csrftoken=%s; expires=%s; Max-Age=31449600; Path=/; SameSite=Lax", csrfRandom(),
				time.Now().Add(31449600*time.Second).UTC().Format(http.TimeFormat))
			if h.csrfCookieSecure {
				cookie += "; Secure"
			}
			c.Response().Header().Add("Set-Cookie", cookie)
			varyAPISchema(c.Response().Header(), "Cookie")
		}
		return next(c)
	}
}

func (h *ChatTestHandler) Page(c echo.Context) error {
	audit, err := h.session(c.Request())
	if err != nil {
		return restError(c, err)
	}
	if audit.ActorID != nil {
		return restError(c, errChatTestTemplate)
	}
	// get_full_path(): the matched path, then '?' and iri_to_uri(QUERY_STRING)
	// when the query is not empty; redirect_to_login quotes it with safe='/'.
	next := "/chat/test/"
	if query := c.Request().URL.RawQuery; query != "" {
		next += "?" + djangoIRIToURI(query)
	}
	header := c.Response().Header()
	header.Set("Content-Type", "text/html; charset=utf-8")
	header.Set("Location", chatTestLoginURL+"?next="+chatTestQuote(next))
	c.Response().WriteHeader(http.StatusFound)
	return nil
}

// RefuseCSRF answers an unsafe request that CsrfViewMiddleware rejects with
// django.views.csrf.csrf_failure: 403 and the failure page (DEBUG off) in the
// active language. kind is middleware.CSRFFailure*.
func (h *ChatTestHandler) RefuseCSRF(c echo.Context, kind string) error {
	messages := csrfFailureCatalogs[handlerReadRouteLocale.Language(c.Request())]
	if messages == nil {
		messages = csrfFailureCatalogs[readRouteDefaultLanguage]
	}
	page := csrfFailureSkeletons[kind]
	for name, text := range messages {
		page = strings.ReplaceAll(page, "@@"+name+"@@", djangoEscapeHTML(text))
	}
	header := c.Response().Header()
	header.Set("Content-Type", "text/html; charset=utf-8")
	header.Set("Content-Length", strconv.Itoa(len(page)))
	c.Response().WriteHeader(http.StatusForbidden)
	if c.Request().Method == http.MethodHead {
		return nil
	}
	_, err := c.Response().Write([]byte(page))
	return err
}

// chatTestQuote is urllib.parse.quote(value, safe='/') of an ASCII string.
func chatTestQuote(value string) string {
	var out strings.Builder
	for i := 0; i < len(value); i++ {
		b := value[i]
		switch {
		case b >= 'A' && b <= 'Z', b >= 'a' && b <= 'z', b >= '0' && b <= '9', strings.IndexByte("_.-~/", b) >= 0:
			out.WriteByte(b)
		default:
			fmt.Fprintf(&out, "%%%02X", b)
		}
	}
	return out.String()
}
