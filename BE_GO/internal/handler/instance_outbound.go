package handler

import (
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// InstanceOutboundHandler serves the owner-only instance operations that
// contact other services (InstanceReleasesView, EmailTestView,
// IntegrationTestView) with the administration handler's identity,
// IsInstanceOwner and error mapping.
type InstanceOutboundHandler struct {
	*InstanceAdminHandler
	outbound *service.InstanceOutbound
}

func NewInstanceOutboundHandler(admin *InstanceAdminHandler, outbound *service.InstanceOutbound) *InstanceOutboundHandler {
	return &InstanceOutboundHandler{InstanceAdminHandler: admin, outbound: outbound}
}

// Diagnostics is DiagnosticsView: GET is the stored snapshot, POST claims the
// lease and runs the probes; both 200s carry Cache-Control: no-store.
func (h *InstanceOutboundHandler) Diagnostics(c echo.Context) error {
	if ok, err := h.begin(c, "GET, POST, HEAD, OPTIONS", "instance_diagnostics", false); !ok {
		return err
	}
	request := c.Request()
	switch {
	case request.Method == http.MethodOptions:
		return supplierOptions(c, "Diagnostics", "")
	case isRead(request.Method):
		body, err := h.admin.Diagnostics(request.Context())
		if err != nil {
			return h.fail(c, err)
		}
		c.Response().Header().Set("Cache-Control", "no-store")
		return c.JSON(200, body)
	case request.Method == http.MethodPost:
		// The view never reads request.data.
		tracked, err := h.operations.Track(request.Context())
		if err != nil {
			return paymentCategoryError(c, err)
		}
		defer tracked.Release()
		ctx, err := tracked.Admit()
		if err != nil {
			return paymentCategoryError(c, err)
		}
		body, err := h.outbound.RunDiagnostics(ctx)
		if err != nil {
			return h.fail(c, err)
		}
		c.Response().Header().Set("Cache-Control", "no-store")
		return c.JSON(200, body)
	}
	return h.notAllowed(c)
}

// Releases is InstanceReleasesView (GET).
func (h *InstanceOutboundHandler) Releases(c echo.Context) error {
	if ok, err := h.begin(c, "GET, HEAD, OPTIONS", "instance_releases", false); !ok {
		return err
	}
	request := c.Request()
	switch {
	case request.Method == http.MethodOptions:
		return supplierOptions(c, "Instance Releases", "")
	case isRead(request.Method):
		// QueryDict.get: the last value.
		query := parseReferenceForm(request.URL.RawQuery)
		page, present := query.Lookup("page")
		refresh, _ := query.Lookup("refresh")
		pageText, _ := page.(string)
		refreshText, _ := refresh.(string)
		body, err := h.outbound.Releases(request.Context(), pageText, present, refreshText)
		return h.answer(c, body, err)
	}
	return h.notAllowed(c)
}

// EmailTest is EmailTestView (POST; Cache-Control: no-store).
func (h *InstanceOutboundHandler) EmailTest(c echo.Context) error {
	if ok, err := h.begin(c, "POST, OPTIONS", "instance_email_test", true); !ok {
		return err
	}
	switch c.Request().Method {
	case http.MethodOptions:
		return supplierOptions(c, "Email Test", "")
	case http.MethodPost:
		return h.body(c, func(data any) error {
			body, err := h.outbound.EmailTest(c.Request().Context(), data)
			return h.answer(c, body, err)
		})
	}
	return h.notAllowed(c)
}

// IntegrationTest is IntegrationTestView (POST; Cache-Control: no-store);
// the provider is resolved after the method.
func (h *InstanceOutboundHandler) IntegrationTest(c echo.Context) error {
	if ok, err := h.begin(c, "POST, OPTIONS", "instance_integration_test", true); !ok {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Integration Test", "")
	}
	if request.Method != http.MethodPost {
		return h.notAllowed(c)
	}
	provider := c.Param("provider")
	if err := h.admin.CheckProvider(provider); err != nil {
		return h.fail(c, err)
	}
	return h.body(c, func(data any) error {
		body, err := h.outbound.IntegrationTest(c.Request().Context(), provider, data)
		return h.answer(c, body, err)
	})
}
