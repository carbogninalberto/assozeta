package handler

import (
	"errors"
	"net/http"
	"regexp"
	"strings"

	"github.com/labstack/echo/v4"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/handoff"
)

// djangoNotFoundPage is django.views.defaults.page_not_found without a
// 404.html template (none exists in BE): ERROR_PAGE_TEMPLATE titled
// "Not Found".
var djangoNotFoundPage = []byte("\n<!doctype html>\n<html lang=\"en\">\n<head>\n  <title>Not Found</title>\n</head>\n<body>\n" +
	"  <h1>Not Found</h1><p>The requested resource was not found on this server.</p>\n</body>\n</html>\n")

// djangoRouterPrefixes are the DefaultRouter registrations of
// BE/application/urls.py; their list and detail routes end with '/'.
const djangoRouterPrefixes = `(?:course-locations|folders|documents|templates|subscription/memberships|course-subscriptions|personas|personas-subscriptions)`

// djangoSlashPatterns are the core.urls patterns that end with '/' (matched
// against the decoded path, as Django's resolver matches path_info). A path
// that resolves to none of Django's patterns but whose slash-terminated form
// matches one of these is CommonMiddleware's APPEND_SLASH redirect.
var djangoSlashPatterns = []*regexp.Regexp{
	regexp.MustCompile(`^/` + djangoRouterPrefixes + `/$`),
	regexp.MustCompile(`^/` + djangoRouterPrefixes + `/[^/.]+/$`), // detail routes; documents/bulk_delete/ too
	regexp.MustCompile(`^/chat/test/$`),
	regexp.MustCompile(`^/schema/(?:swagger-ui/|redoc/)?$`),
	regexp.MustCompile(`^/document/medical/certificate/$`),
	regexp.MustCompile(`^/document/subscription/preview/$`),
	regexp.MustCompile(`^/document/(?:subscription|invoice|einvoice|compensation|template|medical-appointment)/[^/]+/view/$`),
}

// DjangoFallbackRoutes marks Echo routes whose handler answers paths no
// Django pattern resolves as unmatched URLs (the router's wildcard routes).
func DjangoFallbackRoutes(paths []string) {
	for _, path := range paths {
		djangoConverterRoutes[path] = true
	}
}

func djangoSlashResolves(path string) bool {
	for _, pattern := range djangoSlashPatterns {
		if pattern.MatchString(path) {
			return true
		}
	}
	return false
}

// DjangoNotFound answers a URL that no Django pattern resolves: the
// APPEND_SLASH 301 (DEBUG is off, so every method) when the path plus '/'
// resolves, otherwise page_not_found's 404 page. LocaleMiddleware's
// Content-Language and Vary come from the writer DjangoUnmatchedLocale
// installed before CORS, so Vary keeps Django's order.
func DjangoNotFound(c echo.Context) error {
	if writer, ok := c.Get(djangoPageLocaleKey).(*djangoPageLocaleWriter); ok {
		writer.enabled = true
	}
	if path := c.Request().URL.Path; !strings.HasSuffix(path, "/") && djangoSlashResolves(path+"/") {
		return (&APISchemaHandler{}).AppendSlash(c)
	}
	return c.Blob(http.StatusNotFound, "text/html; charset=utf-8", djangoNotFoundPage)
}

const djangoPageLocaleKey = "djangoPageLocale"

// djangoConverterRoutes are the Echo routes whose handlers find a path
// segment that Django's int or uuid converter rejects and then answer as an
// unmatched URL.
var djangoConverterRoutes = map[string]bool{
	"/audit-logs/:id/detail":                    true,
	"/instance/admin/data-restore/:id/download": true,
	"/instance/admin/data-restore/:id/:action":  true,
}

// DjangoUnmatchedLocale must run as a Pre middleware before DjangoCORS: for a
// request Echo routes to the catch-all (or with an empty parameter) it applies
// LocaleMiddleware's Content-Language and Vary as the last response headers,
// and for the converter routes it leaves a dormant writer DjangoNotFound
// enables.
func DjangoUnmatchedLocale(e *echo.Echo) echo.MiddlewareFunc {
	return func(next echo.HandlerFunc) echo.HandlerFunc {
		return func(c echo.Context) error {
			request := c.Request()
			probe := e.NewContext(request, nil)
			e.Router().Find(request.Method, echo.GetPath(request), probe)
			unmatched := probe.Path() == "/*"
			for _, value := range probe.ParamValues() {
				unmatched = unmatched || value == ""
			}
			if unmatched || djangoConverterRoutes[probe.Path()] {
				writer := &djangoPageLocaleWriter{ResponseWriter: c.Response().Writer, language: handlerReadRouteLocale.Language(request), enabled: unmatched}
				c.Response().Writer = writer
				c.Set(djangoPageLocaleKey, writer)
			}
			return next(c)
		}
	}
}

type djangoPageLocaleWriter struct {
	http.ResponseWriter
	language         string
	enabled, written bool
}

func (w *djangoPageLocaleWriter) WriteHeader(status int) {
	if w.enabled && !w.written {
		w.Header().Set("Content-Language", w.language)
		varyAcceptLanguage(w.Header())
	}
	w.written = true
	w.ResponseWriter.WriteHeader(status)
}

func (w *djangoPageLocaleWriter) Write(data []byte) (int, error) {
	if !w.written {
		w.WriteHeader(http.StatusOK)
	}
	return w.ResponseWriter.Write(data)
}

func (w *djangoPageLocaleWriter) Unwrap() http.ResponseWriter { return w.ResponseWriter }

func varyAcceptLanguage(header http.Header) {
	for _, value := range header.Values("Vary") {
		for _, token := range strings.Split(value, ",") {
			if strings.EqualFold(strings.TrimSpace(token), "Accept-Language") {
				return
			}
		}
	}
	header.Add("Vary", "Accept-Language")
}

// DjangoEmptySegment hands a request to unmatched whenever a route parameter
// is empty: Django's str, int, uuid and path converters never match an empty
// segment, while Echo's parameters and wildcards do.
func DjangoEmptySegment(unmatched echo.HandlerFunc) echo.MiddlewareFunc {
	return func(next echo.HandlerFunc) echo.HandlerFunc {
		return func(c echo.Context) error {
			for _, value := range c.ParamValues() {
				if value == "" {
					return unmatched(c)
				}
			}
			return next(c)
		}
	}
}

// DjangoHTTPErrorHandler renders Echo's not-found error (no route, or a
// handler that found its path segment rejected by Django's converter) as
// Django's unmatched-URL answer; other errors keep Echo's default handling.
func DjangoHTTPErrorHandler(e *echo.Echo) echo.HTTPErrorHandler {
	return func(err error, c echo.Context) {
		var httpError *echo.HTTPError
		if errors.As(err, &httpError) && httpError.Code == http.StatusNotFound && !c.Response().Committed {
			if renderErr := DjangoNotFound(c); renderErr != nil {
				e.DefaultHTTPErrorHandler(renderErr, c)
			}
			return
		}
		if !c.Response().Committed && handoff.Serve(c, err) {
			return
		}
		e.DefaultHTTPErrorHandler(err, c)
	}
}
