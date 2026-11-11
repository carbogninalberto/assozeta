package handler

import (
	"errors"
	"net/http"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type ProfileSettingsHandler struct {
	settings   *service.ProfileSettings
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewProfileSettingsHandler(settings *service.ProfileSettings, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *ProfileSettingsHandler {
	return &ProfileSettingsHandler{settings: settings, actors: actors, identities: identities, audit: audit, operations: operations}
}

// admitRead is admitRESTWrite without reading the body (a DRF GET never
// parses it): profile_settings GET can create the card configuration.
func (h *ProfileSettingsHandler) admitRead(c echo.Context) (*restWrite, func(), error) {
	request := c.Request()
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return nil, nil, paymentCategoryError(c, err)
	}
	auditContext, err := h.audit(request)
	if err != nil {
		tracked.Release()
		return nil, nil, restError(c, err)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		tracked.Release()
		return nil, nil, paymentCategoryError(c, err)
	}
	return &restWrite{Context: ctx, Audit: auditContext}, tracked.Release, nil
}

// Settings mirrors profile_settings (@api_view(['GET', 'POST'])).
func (h *ProfileSettingsHandler) Settings(c echo.Context) error {
	c.Response().Header().Set("Allow", "GET, POST, OPTIONS")
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, "profile_settings")
	if identity == nil {
		return err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return supplierOptions(c, "Profile Settings", "")
	case http.MethodGet:
		read, release, err := h.admitRead(c)
		if read == nil {
			return err
		}
		defer release()
		body, err := h.settings.Settings(read.Context, *identity, read.Audit)
		if err != nil {
			return h.failure(c, err)
		}
		return c.JSON(200, body)
	case http.MethodPost:
		write, release, err := admitRESTWrite(c, h.operations, h.audit)
		if write == nil {
			return err
		}
		defer release()
		if err = h.settings.UpdateSettings(write.Context, *identity, write.Body, write.Form, write.Audit); err != nil {
			return h.failure(c, err)
		}
		return c.JSON(200, map[string]string{"status": "success"})
	}
	return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
}

// Integrations mirrors profile_integrations (@api_view(['GET', 'PATCH'])).
func (h *ProfileSettingsHandler) Integrations(c echo.Context) error {
	c.Response().Header().Set("Allow", "GET, PATCH, OPTIONS")
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, "profile_integrations")
	if identity == nil {
		return err
	}
	request := c.Request()
	var result service.ProfileIntegrations
	switch request.Method {
	case http.MethodOptions:
		return supplierOptions(c, "Profile Integrations", "")
	case http.MethodGet:
		if result, err = h.settings.Integrations(request.Context(), *identity); err != nil {
			return h.failure(c, err)
		}
	case http.MethodPatch:
		write, release, err := admitRESTWrite(c, h.operations, h.audit)
		if write == nil {
			return err
		}
		defer release()
		if result, err = h.settings.UpdateIntegrations(write.Context, *identity, write.Body, write.Form, write.Audit); err != nil {
			return h.failure(c, err)
		}
	default:
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	return c.JSON(200, map[string]any{"review_url": profileResponseValue(result.URL), "review_url_enabled": profileResponseValue(result.Enabled)})
}

func (h *ProfileSettingsHandler) failure(c echo.Context, err error) error {
	request := c.Request()
	var invalid *service.ProfileSettingsValidationError
	switch {
	case errors.As(err, &invalid):
		return c.JSON(400, profileSettingsMessages(request, invalid.Fields))
	case errors.Is(err, domain.ErrProfileNotAssociation):
		return restError(c, &domain.PermissionDenied{Detail: "User is not a sport association"})
	case errors.Is(err, service.ErrProfileSettingsForeignCategory):
		return restError(c, &domain.PermissionDenied{Detail: messageReadsPermissionDetail(request)})
	case errors.Is(err, domain.ErrProfileCardMissing):
		return c.JSON(404, map[string]string{"status": "membership card configuration not found"})
	}
	return messageReadsError(c, err)
}

// profileSettingsMessages renders the serializer codes; IntegerField's
// max_value message is not in the shared table.
func profileSettingsMessages(request *http.Request, fields map[string]any) map[string]any {
	english := restLanguage(request) == "en"
	result := make(map[string]any, len(fields))
	for name, value := range fields {
		codes, _ := value.([]string)
		messages := make([]string, 0, len(codes))
		for _, code := range codes {
			if limit, ok := strings.CutPrefix(code, "max-value:"); ok {
				if english {
					messages = append(messages, "Ensure this value is less than or equal to "+limit+".")
				} else {
					messages = append(messages, "Assicurati che il valore sia minore o uguale a "+limit+".")
				}
				continue
			}
			messages = append(messages, paymentCategoryValidationMessage(code, english))
		}
		result[name] = messages
	}
	return result
}

// profileResponseValue renders a request value DRF echoes: parsed objects
// become JSON objects (key order does not matter to clients).
func profileResponseValue(value any) any {
	switch v := value.(type) {
	case domain.RequestObject:
		object := make(map[string]any, len(v))
		for _, field := range v {
			object[field.Name] = profileResponseValue(field.Value)
		}
		return object
	case []any:
		items := make([]any, len(v))
		for index, item := range v {
			items[index] = profileResponseValue(item)
		}
		return items
	}
	return value
}
