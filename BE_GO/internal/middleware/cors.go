package middleware

import (
	"net/http"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/pkg/validate"
	"github.com/labstack/echo/v4"
)

const corsAllowHeaders = "accept, authorization, content-type, user-agent, x-csrftoken, x-requested-with, user-id, x-impersonation-id, x-group-id, x-setup-token"
const corsAllowMethods = "DELETE, GET, OPTIONS, PATCH, POST, PUT"

// DjangoCORS implements the settings actually selected by core.settings. Register
// it with Echo.Pre: preflight must precede routing, session and authentication.
// The immutable policy belongs to this server instance, never a global registry.
func DjangoCORS(allowAll bool, origins []string) echo.MiddlewareFunc {
	allowed := make(map[corsOrigin]bool, len(origins))
	literalNull, invalidPolicy := false, false
	for _, raw := range origins {
		literalNull = literalNull || raw == "null"
		origin, valid := splitCORSOrigin(raw)
		invalidPolicy = invalidPolicy || !valid
		allowed[origin] = valid
	}
	return func(next echo.HandlerFunc) echo.HandlerFunc {
		return func(c echo.Context) error {
			r := c.Request()
			raw := sessionHeader(r, "Origin")
			origin, valid := splitCORSOrigin(raw)
			_, requestedMethod := r.Header["Access-Control-Request-Method"]
			preflight := r.Method == http.MethodOptions && requestedMethod
			nullAllowed := raw == "null" && literalNull
			if raw != "" && valid && !allowAll && !nullAllowed && invalidPolicy {
				// django-cors-headers parses the whole configured list after the
				// view returns. Preserve view effects, then replace the response
				// with its middleware-exception 500. Discard, never buffer, the
				// unusable response; preflight still never enters the view.
				writer := c.Response().Writer
				c.Response().Writer = &corsDiscardWriter{header: make(http.Header)}
				if !preflight {
					_ = next(c)
				}
				c.SetResponse(echo.NewResponse(writer, c.Echo()))
				clear(writer.Header())
				return djangoServerError(c)
			}
			accept := raw != "" && valid && (allowAll || nullAllowed || allowed[origin])
			// Latin-1 is used for comparison, but reflection preserves the
			// original HTTP bytes instead of re-encoding them as UTF-8.
			reflected := strings.Join(r.Header.Values("Origin"), ",")
			c.Response().Writer = &sessionHeaderWriter{ResponseWriter: c.Response().Writer, before: func() {
				h := c.Response().Header()
				varyHeader(h, "origin")
				if !accept {
					return
				}
				if allowAll {
					h.Set("Access-Control-Allow-Origin", "*")
				} else {
					h.Set("Access-Control-Allow-Origin", reflected)
				}
				if r.Method == http.MethodOptions {
					h.Set("Access-Control-Allow-Headers", corsAllowHeaders)
					h.Set("Access-Control-Allow-Methods", corsAllowMethods)
					h.Set("Access-Control-Max-Age", "86400")
				}
			}}
			if preflight {
				c.Response().Header().Set("Content-Length", "0")
				return c.Blob(http.StatusOK, "text/html; charset=utf-8", nil)
			}
			return next(c)
		}
	}
}

type corsDiscardWriter struct{ header http.Header }

func (w *corsDiscardWriter) Header() http.Header         { return w.header }
func (w *corsDiscardWriter) WriteHeader(int)             {}
func (w *corsDiscardWriter) Write(p []byte) (int, error) { return len(p), nil }
func (w *corsDiscardWriter) Flush()                      {}

type corsOrigin struct{ scheme, netloc string }

func splitCORSOrigin(raw string) (corsOrigin, bool) {
	origin, valid := validate.PythonURLOrigin(raw)
	return corsOrigin{scheme: origin.Scheme, netloc: origin.Authority}, valid
}
