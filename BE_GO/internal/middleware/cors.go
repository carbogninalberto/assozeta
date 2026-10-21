package middleware

import (
	"net/http"
	"net/netip"
	"strings"

	"github.com/labstack/echo/v4"
	"golang.org/x/text/unicode/norm"
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

// Only the scheme/netloc and ValueError boundary of shipped Python 3.13.2 are
// needed. net/url.Parse is not equivalent: Django accepts invalid ports and
// literal percent escapes here, without looking up hostname or port properties.
func splitCORSOrigin(raw string) (corsOrigin, bool) {
	value := strings.TrimLeftFunc(raw, func(r rune) bool { return r <= ' ' })
	value = strings.NewReplacer("\t", "", "\r", "", "\n", "").Replace(value)
	var result corsOrigin
	if colon := strings.IndexByte(value, ':'); colon > 0 && asciiAlpha(value[0]) {
		validScheme := true
		for i := 1; i < colon; i++ {
			b := value[i]
			if !asciiAlpha(b) && !(b >= '0' && b <= '9') && b != '+' && b != '-' && b != '.' {
				validScheme = false
				break
			}
		}
		if validScheme {
			result.scheme, value = strings.ToLower(value[:colon]), value[colon+1:]
		}
	}
	if strings.HasPrefix(value, "//") {
		result.netloc = value[2:]
		if end := strings.IndexAny(result.netloc, "/?#"); end >= 0 {
			result.netloc = result.netloc[:end]
		}
	}
	left, right := strings.IndexByte(result.netloc, '['), strings.IndexByte(result.netloc, ']')
	if (left < 0) != (right < 0) {
		return corsOrigin{}, false
	}
	if left >= 0 {
		// CPython's bracketed-netloc check uses the hostname after the last
		// userinfo separator, and rejects text around bracketed hosts. Older
		// Python 3.12.6 accepted malformed prefixes/suffixes here.
		hostAndPort := result.netloc
		if at := strings.LastIndexByte(hostAndPort, '@'); at >= 0 {
			hostAndPort = hostAndPort[at+1:]
		}
		before, bracketed, open := strings.Cut(hostAndPort, "[")
		var host string
		if open {
			if before != "" {
				return corsOrigin{}, false
			}
			var port string
			host, port, _ = strings.Cut(bracketed, "]")
			if port != "" && !strings.HasPrefix(port, ":") {
				return corsOrigin{}, false
			}
		} else {
			host, _, _ = strings.Cut(hostAndPort, ":")
		}
		if strings.HasPrefix(host, "v") {
			version, address, dot := strings.Cut(host[1:], ".")
			if !dot || version == "" || address == "" {
				return corsOrigin{}, false
			}
			for _, r := range version {
				if !(r >= 'a' && r <= 'f' || r >= 'A' && r <= 'F' || r >= '0' && r <= '9') {
					return corsOrigin{}, false
				}
			}
		} else {
			ip, err := netip.ParseAddr(host)
			if err != nil || !ip.Is6() || strings.Count(host, "%") > 1 {
				return corsOrigin{}, false
			}
		}
	}
	stripped := strings.NewReplacer("@", "", ":", "", "#", "", "?", "").Replace(result.netloc)
	if normalized := norm.NFKC.String(stripped); normalized != stripped && strings.ContainsAny(normalized, "/?#@:") {
		return corsOrigin{}, false
	}
	return result, true
}

func asciiAlpha(b byte) bool { return b >= 'a' && b <= 'z' || b >= 'A' && b <= 'Z' }
