package handler

import (
	"errors"
	"net/http"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// InstanceAdminHandler serves the owner-only installation administration
// APIViews of BE/instance: authentication and identity, IsInstanceOwner on
// the authenticated actor, then method dispatch (DRF checks permissions
// before a 405). The email and integration views add Cache-Control: no-store
// to every response (OwnerOperationsView.finalize_response).
type InstanceAdminHandler struct {
	admin      *service.InstanceAdmin
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewInstanceAdminHandler(admin *service.InstanceAdmin, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *InstanceAdminHandler {
	return &InstanceAdminHandler{admin: admin, actors: actors, identities: identities, audit: audit, operations: operations}
}

const instanceAdminDenied = "Only the instance owner or an administrator can administer this installation."

// begin runs DRF's initial(): it returns false when a response was written.
func (h *InstanceAdminHandler) begin(c echo.Context, allow, operation string, noStore bool) (bool, error) {
	c.Response().Header().Set("Allow", allow)
	if noStore {
		c.Response().Header().Set("Cache-Control", "no-store")
	}
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, operation)
	if identity == nil {
		return false, err
	}
	allowed, err := h.admin.Allowed(c.Request().Context(), *identity.Actor)
	if err != nil {
		return false, h.fail(c, err)
	}
	if !allowed {
		return false, c.JSON(403, map[string]string{"detail": instanceAdminDenied})
	}
	c.Set(instanceAdminActorKey, identity.Actor.ID)
	return true, nil
}

// instanceAdminActorKey holds the authenticated actor's id (actor_of(request)).
const instanceAdminActorKey = "instance_admin_actor"

// body reads request.data for a write. A form or multipart body is normalised
// as DRF reads an HTML form; form is the view's serializer description.
func (h *InstanceAdminHandler) body(c echo.Context, form func(any) (any, error), run func(any) error) error {
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	data := write.Body
	if write.Form {
		if data, err = form(data); err != nil {
			return h.fail(c, err)
		}
	}
	c.SetRequest(c.Request().WithContext(write.Context))
	return run(data)
}

func (h *InstanceAdminHandler) notAllowed(c echo.Context) error {
	return restError(c, &restInputError{Status: 405, Detail: restText(c.Request(), "method")})
}

func (h *InstanceAdminHandler) fail(c echo.Context, err error) error {
	var invalid *dto.InstanceAdminInvalid
	var response *service.InstanceAdminResponse
	var input *restInputError
	switch {
	case errors.As(err, &invalid):
		return c.JSON(400, instanceAdminMessages(c.Request(), invalid.Body))
	case errors.As(err, &response):
		if response.RetryAfter != "" {
			c.Response().Header().Set("Retry-After", response.RetryAfter)
		}
		return c.JSON(response.Status, response.Body)
	case errors.Is(err, service.ErrInstanceAdminProvider):
		return c.JSON(404, map[string]string{"detail": restText(c.Request(), "notfound")})
	case errors.As(err, &input):
		return restError(c, err)
	}
	// An unhandled exception: Django's error page has no DRF headers.
	c.Response().Header().Del("Cache-Control")
	return restError(c, err)
}

func (h *InstanceAdminHandler) answer(c echo.Context, body any, err error) error {
	if err != nil {
		return h.fail(c, err)
	}
	return c.JSON(200, body)
}

func isRead(method string) bool { return method == http.MethodGet || method == http.MethodHead }

// Admin is InstanceAdminView (GET, PUT).
func (h *InstanceAdminHandler) Admin(c echo.Context) error {
	if ok, err := h.begin(c, "GET, PUT, HEAD, OPTIONS", "instance_admin", false); !ok {
		return err
	}
	request := c.Request()
	switch {
	case request.Method == http.MethodOptions:
		return supplierOptions(c, "Instance Admin", "")
	case isRead(request.Method):
		body, err := h.admin.Admin(request.Context())
		return h.answer(c, body, err)
	case request.Method == http.MethodPut:
		write, release, err := admitRESTWrite(c, h.operations, h.audit)
		if write == nil {
			return err
		}
		defer release()
		data := write.Body
		if write.Form {
			if data, err = dto.InstanceAdminBrandingForm(data); err != nil {
				return h.fail(c, err)
			}
		}
		c.SetRequest(c.Request().WithContext(write.Context))
		actor, _ := c.Get(instanceAdminActorKey).(string)
		body, err := h.admin.Branding(c.Request().Context(), data, actor, write.Audit)
		return h.answer(c, body, err)
	}
	return h.notAllowed(c)
}

// Email is EmailSettingsView (GET, PUT, DELETE).
func (h *InstanceAdminHandler) Email(c echo.Context) error {
	if ok, err := h.begin(c, "GET, PUT, DELETE, HEAD, OPTIONS", "instance_email_settings", true); !ok {
		return err
	}
	request := c.Request()
	switch {
	case request.Method == http.MethodOptions:
		return supplierOptions(c, "Email Settings", "")
	case isRead(request.Method):
		body, err := h.admin.Email(request.Context())
		return h.answer(c, body, err)
	case request.Method == http.MethodPut:
		return h.body(c, dto.InstanceAdminEmailForm, func(data any) error {
			body, err := h.admin.PutEmail(c.Request().Context(), data)
			return h.answer(c, body, err)
		})
	case request.Method == http.MethodDelete:
		return h.body(c, dto.InstanceAdminRevisionForm, func(data any) error {
			body, err := h.admin.DeleteEmail(c.Request().Context(), data)
			return h.answer(c, body, err)
		})
	}
	return h.notAllowed(c)
}

// Integration is IntegrationSettingsView (GET, PUT, DELETE).
func (h *InstanceAdminHandler) Integration(c echo.Context) error {
	if ok, err := h.begin(c, "GET, PUT, DELETE, HEAD, OPTIONS", "instance_integration_settings", true); !ok {
		return err
	}
	request := c.Request()
	provider := c.Param("provider")
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Integration Settings", "")
	}
	if !isRead(request.Method) && request.Method != http.MethodPut && request.Method != http.MethodDelete {
		return h.notAllowed(c)
	}
	if err := h.admin.CheckProvider(provider); err != nil {
		return h.fail(c, err)
	}
	switch request.Method {
	case http.MethodPut:
		form := func(data any) (any, error) { return dto.InstanceAdminIntegrationForm(provider, data) }
		return h.body(c, form, func(data any) error {
			body, err := h.admin.PutIntegration(c.Request().Context(), provider, data)
			return h.answer(c, body, err)
		})
	case http.MethodDelete:
		return h.body(c, dto.InstanceAdminRevisionForm, func(data any) error {
			body, err := h.admin.DeleteIntegration(c.Request().Context(), provider, data)
			return h.answer(c, body, err)
		})
	}
	body, err := h.admin.Integration(request.Context(), provider)
	return h.answer(c, body, err)
}

// Updates is InstanceUpdatesView (GET, POST).
func (h *InstanceAdminHandler) Updates(c echo.Context) error {
	if ok, err := h.begin(c, "GET, POST, HEAD, OPTIONS", "instance_updates", false); !ok {
		return err
	}
	request := c.Request()
	switch {
	case request.Method == http.MethodOptions:
		return supplierOptions(c, "Instance Updates", "")
	case isRead(request.Method):
		body, err := h.admin.UpdatesStatus(request.Context())
		return h.answer(c, body, err)
	case request.Method == http.MethodPost:
		actor, _ := c.Get(instanceAdminActorKey).(string)
		return h.body(c, dto.InstanceAdminUpdateForm, func(data any) error {
			return h.fail(c, h.admin.RequestUpdateFor(c.Request().Context(), actor, data))
		})
	}
	return h.notAllowed(c)
}

// Restarts is InstanceRestartsView (POST).
func (h *InstanceAdminHandler) Restarts(c echo.Context) error {
	if ok, err := h.begin(c, "POST, OPTIONS", "instance_restarts", false); !ok {
		return err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return supplierOptions(c, "Instance Restarts", "")
	case http.MethodPost:
		actor, _ := c.Get(instanceAdminActorKey).(string)
		return h.body(c, dto.InstanceAdminRestartForm, func(data any) error {
			return h.fail(c, h.admin.RequestRestartFor(c.Request().Context(), actor, data))
		})
	}
	return h.notAllowed(c)
}

func instanceAdminMessages(request *http.Request, value any) any {
	english := restLanguage(request) == "en"
	switch value := value.(type) {
	case map[string]any:
		result := make(map[string]any, len(value))
		for key, item := range value {
			result[key] = instanceAdminMessages(request, item)
		}
		return result
	case []string:
		result := make([]string, 0, len(value))
		for _, code := range value {
			result = append(result, instanceAdminMessage(code, english))
		}
		return result
	}
	return value
}

func instanceAdminMessage(code string, english bool) string {
	switch {
	case strings.HasPrefix(code, "max-value:"):
		limit := strings.TrimPrefix(code, "max-value:")
		if english {
			return "Ensure this value is less than or equal to " + limit + "."
		}
		return "Assicurati che il valore sia minore o uguale a " + limit + "."
	case code == dto.InstanceAdminRegex:
		if english {
			return "This value does not match the required pattern."
		}
		return "Questo valore non corrisponde alla sequenza richiesta."
	case code == dto.InstanceAdminURL:
		if english {
			return "Enter a valid URL."
		}
		return "Inserisci un URL valido"
	}
	return paymentCategoryValidationMessage(code, english)
}
