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
	textunicode "golang.org/x/text/encoding/unicode"
)

type ImpersonationManager interface {
	Admit(context.Context, *domain.Account, bool, string) (bool, error)
	List(context.Context, *domain.Account, domain.RequestObject) (dto.ImpersonationList, error)
	Current(context.Context, *domain.Account, string) (dto.ImpersonationCurrent, error)
	Start(context.Context, *domain.Account, any, string, domain.LoginAuditContext) (dto.ImpersonationStarted, error)
	End(context.Context, *domain.Account, string, domain.LoginAuditContext) error
}
type ImpersonationManagementHandler struct {
	manager    ImpersonationManager
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
}

func NewImpersonationManagementHandler(manager ImpersonationManager, actors *service.ActorAuthentication, identities *service.IdentityResolver, audit LoginAuditReader) *ImpersonationManagementHandler {
	return &ImpersonationManagementHandler{manager, actors, identities, audit}
}
func (h *ImpersonationManagementHandler) Users(c echo.Context) error   { return h.render(c, true) }
func (h *ImpersonationManagementHandler) Session(c echo.Context) error { return h.render(c, false) }
func (h *ImpersonationManagementHandler) render(c echo.Context, users bool) error {
	original := c.Request()
	request := original.Clone(original.Context())
	// Django ASGI joins repeated request headers. Keep that normalization local
	// and retain the original request for any caller after this view completes.
	for key, values := range original.Header {
		request.Header.Set(key, strings.Join(values, ","))
	}
	c.SetRequest(request)
	defer c.SetRequest(original)
	return ManualResponseEncoding()(func(c echo.Context) error { return h.dispatch(c, users) })(c)
}
func (h *ImpersonationManagementHandler) dispatch(c echo.Context, users bool) error {
	r := c.Request()
	allow := "GET, POST, DELETE, OPTIONS"
	name := "Impersonation Session"
	if users {
		allow = "GET, OPTIONS"
		name = "Impersonation Users"
	}
	c.Response().Header().Set("Allow", allow)
	c.Response().Header().Set("Content-Language", restLanguage(r))
	query := parseImpersonationQuery(r.URL.RawQuery)
	if format, present := query.Lookup("format"); present && format != "" && format != "json" {
		return restError(c, &restInputError{Status: 404, Detail: restText(r, "notfound")})
	}
	if !acceptsJSON(r.Header.Get("Accept")) {
		return restError(c, &restInputError{Status: 406, Detail: restText(r, "accept")})
	}
	actor, err := h.actors.Authenticate(r.Context(), r.Header.Get("Authorization"))
	if err != nil {
		return restError(c, err)
	}
	// ScopedJWTAuthentication resolves a genuine actor before this custom
	// permission class. Anonymous requests do not execute IsAuthenticated's
	// additional identity resolution or body/reference parsing.
	if actor != nil {
		_, err = h.identities.Resolve(r.Context(), actor, domain.IdentityRequest{Method: r.Method, Path: r.URL.Path, SessionID: r.Header.Get("X-Impersonation-Id"), TargetID: r.Header.Get("User-Id"), GroupID: r.Header.Get("X-Group-Id")})
		if err != nil {
			return restError(c, err)
		}
	}
	allowed, err := h.manager.Admit(r.Context(), actor, strings.HasPrefix(r.URL.Path, "/administration/"), r.Method)
	if err != nil {
		return restError(c, err)
	}
	if !allowed {
		if actor == nil {
			c.Response().Header().Set("WWW-Authenticate", `Bearer realm="api"`)
			return restError(c, &restInputError{Status: 401, Detail: restText(r, "credentials")})
		}
		detail := "You do not have permission to perform this action."
		if restLanguage(r) == "it" {
			detail = "Non hai l'autorizzazione per eseguire questa azione."
		}
		return restError(c, &domain.PermissionDenied{Detail: detail})
	}
	if r.Method == http.MethodOptions {
		return c.JSON(200, map[string]any{"name": name, "description": "", "renders": []string{"application/json"}, "parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}})
	}
	if r.Method != http.MethodGet && (users || (r.Method != http.MethodPost && r.Method != http.MethodDelete)) {
		return restError(c, &restInputError{Status: 405, Detail: restText(r, "method")})
	}
	var result any
	if users {
		result, err = h.manager.List(r.Context(), actor, query)
	} else if r.Method == http.MethodGet {
		result, err = h.manager.Current(r.Context(), actor, r.Header.Get("X-Impersonation-Id"))
	} else {
		var body any
		if r.Method == http.MethodPost {
			body, err = readAuthenticatedRESTBody(c)
			if err != nil {
				return restError(c, err)
			}
		}
		audit, auditErr := h.audit(r)
		if auditErr != nil {
			return restError(c, auditErr)
		}
		if r.Method == http.MethodDelete {
			err = h.manager.End(r.Context(), actor, r.Header.Get("X-Impersonation-Id"), audit)
		} else {
			result, err = h.manager.Start(r.Context(), actor, body, r.Header.Get("X-Impersonation-Id"), audit)
		}
	}
	if err != nil {
		var invalid *domain.ImpersonationValidationError
		if errors.As(err, &invalid) {
			return c.JSON(400, []string{invalid.Detail})
		}
		return restError(c, err)
	}
	if r.Method == http.MethodDelete {
		return c.NoContent(204)
	}
	status := 200
	if r.Method == http.MethodPost {
		status = 201
	}
	return c.JSON(status, result)
}

// QueryDict decodes valid percent escapes while retaining malformed escapes,
// replaces malformed UTF-8, and collapses repeated decoded keys to their last
// value. Keep this compatibility boundary local to management views.
func parseImpersonationQuery(raw string) domain.RequestObject {
	decode := func(value string) string {
		hex := func(c byte) (byte, bool) {
			switch {
			case c >= '0' && c <= '9':
				return c - '0', true
			case c >= 'a' && c <= 'f':
				return c - 'a' + 10, true
			case c >= 'A' && c <= 'F':
				return c - 'A' + 10, true
			}
			return 0, false
		}
		decoded := make([]byte, 0, len(value))
		for i := 0; i < len(value); i++ {
			if value[i] == '+' {
				decoded = append(decoded, ' ')
				continue
			}
			if value[i] == '%' && i+2 < len(value) {
				hi, hiOK := hex(value[i+1])
				lo, loOK := hex(value[i+2])
				if hiOK && loOK {
					decoded = append(decoded, hi<<4|lo)
					i += 2
					continue
				}
			}
			decoded = append(decoded, value[i])
		}
		// The UTF-8 decoder replaces malformed sequences without returning an error.
		text, _ := textunicode.UTF8.NewDecoder().String(string(decoded))
		return text
	}
	result := domain.RequestObject{}
	positions := map[string]int{}
	for _, pair := range strings.Split(raw, "&") {
		if pair == "" {
			continue
		}
		key, value, _ := strings.Cut(pair, "=")
		key, value = decode(key), decode(value)
		if position, ok := positions[key]; ok {
			result[position].Value = value
		} else {
			positions[key] = len(result)
			result = append(result, domain.RequestField{Name: key, Value: value})
		}
	}
	return result
}
