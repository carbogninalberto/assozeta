package middleware

import (
	"bufio"
	"context"
	"encoding/json"
	"net"
	"net/http"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/jwt"
	"github.com/labstack/echo/v4"
)

type SessionAuditResolver interface {
	Resolve(context.Context, string) (domain.SessionIdentity, error)
}
type sessionAuditKey struct{}

// SessionAuditContext is suitable for LoginAuditReader. Body, Bearer and scope
// headers never establish this pre-view actor; only the middleware can supply it.
func SessionAuditContext(r *http.Request) (domain.LoginAuditContext, error) {
	value, ok := r.Context().Value(sessionAuditKey{}).(domain.LoginAuditContext)
	if !ok {
		return domain.LoginAuditContext{}, domain.ErrInvalidScopedRequest
	}
	return value, nil
}

// DjangoSessionAudit composes existing-session identity and audit metadata with
// current cookie deletion/Vary policy. Session writes/rotation and the rest of
// the global middleware chain require separate composition.
func DjangoSessionAudit(resolver SessionAuditResolver) echo.MiddlewareFunc {
	return func(next echo.HandlerFunc) echo.HandlerFunc {
		return func(c echo.Context) error {
			r := c.Request()
			// ConditionalSessionMiddleware skips raw /api/ Bearer requests, then
			// Django AuthenticationMiddleware fails because no session exists.
			// Public proxy stripping is a separate routing concern.
			if strings.HasPrefix(r.URL.Path, "/api/") && strings.HasPrefix(sessionHeader(r, "Authorization"), "Bearer ") {
				return djangoServerError(c)
			}
			key, supplied := sessionCookie(sessionHeader(r, "Cookie"))
			identity, err := resolver.Resolve(r.Context(), key)
			c.Response().Writer = &sessionHeaderWriter{ResponseWriter: c.Response().Writer, before: func() {
				varySessionCookie(c.Response().Header())
				if supplied && identity.Empty {
					header := c.Response().Header()
					cookies := header.Values("Set-Cookie")
					header.Del("Set-Cookie")
					for _, cookie := range cookies {
						name, _, _ := strings.Cut(cookie, "=")
						if strings.TrimSpace(name) != "sessionid" {
							header.Add("Set-Cookie", cookie)
						}
					}
					c.Response().Header().Add("Set-Cookie", `sessionid=""; expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=0; Path=/; SameSite=Lax`)
				}
			}}
			if err != nil {
				// ASGI's convert_exception_to_response wraps AuditlogMiddleware;
				// its lazy-user errors never reach ExceptionHandlerMiddleware.
				return djangoServerError(c)
			}
			audit := requestAuditMetadata(r)
			if identity.Actor != nil {
				id, email := identity.Actor.ID, identity.Actor.Email
				audit.ActorID, audit.ActorEmail = &id, &email
			}
			c.SetRequest(r.WithContext(context.WithValue(r.Context(), sessionAuditKey{}, audit)))
			return next(c)
		}
	}
}

// Echo's Before hook is bypassed by an early Flush. Apply session headers at the
// writer boundary so normal writes, returned errors and streamed frames agree.
// Unwrap preserves ResponseController deadlines and protocol capabilities.
type sessionHeaderWriter struct {
	http.ResponseWriter
	before             func()
	applied, committed bool
}

func (w *sessionHeaderWriter) apply() {
	if !w.applied {
		w.applied = true
		w.before()
	}
}
func (w *sessionHeaderWriter) WriteHeader(status int) {
	if w.committed {
		return
	}
	if status >= 100 && status < 200 && status != 101 {
		w.ResponseWriter.WriteHeader(status)
		return
	}
	w.apply()
	w.committed = true
	w.ResponseWriter.WriteHeader(status)
}
func (w *sessionHeaderWriter) Write(data []byte) (int, error) {
	if !w.committed {
		w.apply()
		w.committed = true
	}
	return w.ResponseWriter.Write(data)
}
func (w *sessionHeaderWriter) FlushError() error {
	if !w.committed {
		w.WriteHeader(http.StatusOK)
	}
	return http.NewResponseController(w.ResponseWriter).Flush()
}
func (w *sessionHeaderWriter) Flush() { _ = w.FlushError() }
func (w *sessionHeaderWriter) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	w.apply()
	return http.NewResponseController(w.ResponseWriter).Hijack()
}
func (w *sessionHeaderWriter) Push(target string, options *http.PushOptions) error {
	if pusher, ok := w.ResponseWriter.(http.Pusher); ok {
		return pusher.Push(target, options)
	}
	return http.ErrNotSupported
}
func (w *sessionHeaderWriter) Unwrap() http.ResponseWriter { return w.ResponseWriter }

func requestAuditMetadata(r *http.Request) domain.LoginAuditContext {
	var audit domain.LoginAuditContext
	forwarded := sessionHeader(r, "X-Forwarded-For")
	if forwarded != "" {
		address, _, _ := strings.Cut(forwarded, ",")
		if strings.Contains(address, ".") && strings.Contains(address, ":") {
			address, _, _ = strings.Cut(address, ":")
		} else if strings.Contains(address, "[") {
			_, size := utf8.DecodeRuneInString(address)
			address, _, _ = strings.Cut(address[size:], "]")
		}
		audit.RemoteAddress = &address
	} else if r.RemoteAddr != "" {
		address := r.RemoteAddr
		if host, _, err := net.SplitHostPort(address); err == nil {
			address = host
		}
		audit.RemoteAddress = &address
	}
	if port, ok := jwt.IntegerClaim(sessionHeader(r, "X-Forwarded-Port")); ok {
		value := json.Number(port.String())
		audit.RemotePort = &value
	}
	if _, present := r.Header[http.CanonicalHeaderKey("X-Correlation-Id")]; present {
		value := sessionHeader(r, "X-Correlation-Id")
		audit.CorrelationID = &value
	}
	return audit
}

// ASGIRequest decodes all header bytes as Latin-1, including Cookie.
func sessionHeader(r *http.Request, name string) string {
	raw := strings.Join(r.Header.Values(name), ",")
	var value strings.Builder
	for i := 0; i < len(raw); i++ {
		value.WriteRune(rune(raw[i]))
	}
	return value.String()
}

func varySessionCookie(header http.Header) {
	var values []string
	if existing := header.Values("Vary"); len(existing) > 0 {
		values = strings.Split(strings.Join(existing, ", "), ",")
		space := func(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }
		for i, value := range values {
			if i > 0 {
				value = strings.TrimLeftFunc(value, space)
			}
			if i < len(values)-1 {
				value = strings.TrimRightFunc(value, space)
			}
			values[i] = value
		}
	}
	hasCookie := false
	for _, value := range values {
		if value == "*" {
			header.Set("Vary", "*")
			return
		}
		hasCookie = hasCookie || strings.EqualFold(value, "Cookie")
	}
	if !hasCookie {
		values = append(values, "Cookie")
	}
	header.Set("Vary", strings.Join(values, ", "))
}

func sessionCookie(raw string) (string, bool) {
	var key string
	found := false
	strip := func(s string) string {
		return strings.TrimFunc(s, func(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f })
	}
	for _, chunk := range strings.Split(raw, ";") {
		name, value, ok := strings.Cut(chunk, "=")
		if ok && strip(name) == "sessionid" {
			key = unquoteSessionCookie(strip(value))
			found = true
		}
	}
	return key, found
}

func unquoteSessionCookie(value string) string {
	if len(value) < 2 || value[0] != '"' || value[len(value)-1] != '"' {
		return value
	}
	runes := []rune(value[1 : len(value)-1])
	var out strings.Builder
	for i := 0; i < len(runes); i++ {
		if runes[i] == '\\' && i+1 < len(runes) && runes[i+1] != '\n' {
			i++
			if i+2 < len(runes) && runes[i] >= '0' && runes[i] <= '3' && runes[i+1] >= '0' && runes[i+1] <= '7' && runes[i+2] >= '0' && runes[i+2] <= '7' {
				out.WriteRune((runes[i]-'0')*64 + (runes[i+1]-'0')*8 + runes[i+2] - '0')
				i += 2
				continue
			}
		}
		out.WriteRune(runes[i])
	}
	return out.String()
}
