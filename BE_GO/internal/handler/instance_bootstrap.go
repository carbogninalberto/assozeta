package handler

import (
	"context"
	"errors"
	"net/http"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type InstanceBootstrapReader interface {
	Status(context.Context) (*dto.InstanceStatus, error)
	Config(context.Context) (*dto.InstanceConfig, error)
	Access(context.Context, domain.Account) (*dto.InstanceAccess, error)
}
type InstanceBootstrapHandler struct {
	reader     InstanceBootstrapReader
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewInstanceBootstrapHandler(reader InstanceBootstrapReader, actors *service.ActorAuthentication, identities *service.IdentityResolver) *InstanceBootstrapHandler {
	return &InstanceBootstrapHandler{reader: reader, actors: actors, identities: identities}
}
func (h *InstanceBootstrapHandler) Status(c echo.Context) error { return h.read(c, "status") }
func (h *InstanceBootstrapHandler) Config(c echo.Context) error { return h.read(c, "config") }
func (h *InstanceBootstrapHandler) Access(c echo.Context) error { return h.read(c, "access") }
func (h *InstanceBootstrapHandler) read(c echo.Context, endpoint string) error {
	request := c.Request()
	c.Response().Header().Set("Allow", "GET, HEAD, OPTIONS")
	c.Response().Header().Set("Content-Language", restLanguage(request))
	if !acceptsJSON(request.Header.Get("Accept")) {
		return restError(c, &restInputError{Status: 406, Detail: restText(request, "accept")})
	}
	query := parseReferenceForm(request.URL.RawQuery)
	if format, ok := query.Lookup("format"); ok && format != "json" {
		return restError(c, &restInputError{Status: 404, Detail: restText(request, "notfound")})
	}
	var actor *domain.Account
	if endpoint == "access" {
		var err error
		actor, err = h.actors.Authenticate(request.Context(), request.Header.Get("Authorization"))
		if err != nil {
			return restError(c, err)
		}
		// ScopedJWTAuthentication is called only for an authenticated actor. The
		// standard IsAuthenticated view then rejects anonymous requests before scope
		// resolution, including when an anonymous caller forges scope headers.
		if actor == nil {
			c.Response().Header().Set("WWW-Authenticate", `Bearer realm="api"`)
			return restError(c, &restInputError{Status: 401, Detail: restText(request, "credentials")})
		}
		_, err = h.identities.Resolve(request.Context(), actor, domain.IdentityRequest{Method: request.Method, Path: request.URL.Path, SessionID: request.Header.Get("X-Impersonation-Id"), TargetID: request.Header.Get("User-Id"), GroupID: request.Header.Get("X-Group-Id")})
		if err != nil {
			return restError(c, err)
		}
	}
	if request.Method == http.MethodOptions {
		name, description := "Instance Access", ""
		if endpoint == "status" {
			name = "Instance Status"
			description = "GET /instance/status\nCheck if instance is configured.\nPublic endpoint - no authentication required."
		}
		if endpoint == "config" {
			name = "Instance Config"
			description = "GET /instance/config\nGet full instance configuration for frontend.\nPublic endpoint - no authentication required."
		}
		return c.JSON(200, map[string]any{"name": name, "description": description, "renders": []string{"application/json"}, "parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}})
	}
	if request.Method != http.MethodGet && request.Method != http.MethodHead {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	var result any
	var err error
	switch endpoint {
	case "status":
		result, err = h.reader.Status(request.Context())
	case "config":
		result, err = h.reader.Config(request.Context())
	case "access":
		result, err = h.reader.Access(request.Context(), *actor)
	}
	if errors.Is(err, domain.ErrInstanceNotConfigured) {
		return c.JSON(404, map[string]string{"error": "Instance not configured"})
	}
	if err != nil {
		return restError(c, err)
	}
	return c.JSON(200, result)
}

// InstanceBootstrapLocale preserves LocaleMiddleware on these new public and
// protected reads, including responses short-circuited by CORS or maintenance.
// It performs no authentication, parsing or database work.
func InstanceBootstrapLocale() echo.MiddlewareFunc {
	return func(next echo.HandlerFunc) echo.HandlerFunc {
		return func(c echo.Context) error {
			if c.Request().URL.Path == "/manuale/sections" || strings.HasPrefix(c.Request().URL.Path, "/manuale/assets/") {
				c.Response().Writer = &instanceLocaleWriter{ResponseWriter: c.Response().Writer, language: restLanguage(c.Request())}
			}
			switch c.Request().URL.Path {
			case "/instance/status", "/instance/config", "/instance/access":
				c.Response().Writer = &instanceLocaleWriter{ResponseWriter: c.Response().Writer, language: restLanguage(c.Request())}
			}
			return next(c)
		}
	}
}

// Apply the locale at the response boundary: downstream configuration failures
// can deliberately clear headers before writing their error response.
type instanceLocaleWriter struct {
	http.ResponseWriter
	language string
	written  bool
}

func (w *instanceLocaleWriter) WriteHeader(status int) {
	if !w.written {
		w.Header().Set("Content-Language", w.language)
		found := false
		for _, value := range w.Header().Values("Vary") {
			for _, token := range strings.Split(value, ",") {
				if strings.EqualFold(strings.TrimSpace(token), "Accept-Language") {
					found = true
				}
			}
		}
		if !found {
			w.Header().Add("Vary", "Accept-Language")
		}
		w.written = true
	}
	w.ResponseWriter.WriteHeader(status)
}
func (w *instanceLocaleWriter) Write(data []byte) (int, error) {
	if !w.written {
		w.WriteHeader(http.StatusOK)
	}
	return w.ResponseWriter.Write(data)
}
func (w *instanceLocaleWriter) Unwrap() http.ResponseWriter { return w.ResponseWriter }
