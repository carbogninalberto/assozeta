package handler

import (
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// InvoiceViewsHandler serves document_invoice_view and document_einvoice_view
// (BE/docmanager/views/printing_views.py, @api_view(['GET']) with
// IsAuthenticated | HasBypassAuthorizationHeader): identity resolution, the
// permission classes, method dispatch, then the view.
type InvoiceViewsHandler struct {
	views      *service.InvoiceViews
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewInvoiceViewsHandler(views *service.InvoiceViews, actors *service.ActorAuthentication,
	identities *service.IdentityResolver) *InvoiceViewsHandler {
	return &InvoiceViewsHandler{views: views, actors: actors, identities: identities}
}

func (h *InvoiceViewsHandler) Invoice(c echo.Context) error {
	return h.serve(c, service.ViewInvoice, "document_invoice_view", "Document Invoice View")
}

func (h *InvoiceViewsHandler) Einvoice(c echo.Context) error {
	return h.serve(c, service.ViewEinvoice, "document_einvoice_view", "Document Einvoice View")
}

func (h *InvoiceViewsHandler) serve(c echo.Context, view, operation, name string) error {
	request := c.Request()
	identity, err := messageMutationsIdentityFor(c, h.actors, h.identities, http.MethodGet, operation, true)
	if identity == nil {
		return err
	}
	if identity.ScopeUser == nil {
		allowed, err := h.views.AuthorizeBypass(requestAuthorization(request))
		if err != nil {
			return messageReadsError(c, err)
		}
		if !allowed {
			c.Response().Header().Set("WWW-Authenticate", `Bearer realm="api"`)
			return restError(c, &restInputError{Status: 401, Detail: restText(request, "credentials")})
		}
	}
	switch request.Method {
	case http.MethodOptions:
		return supplierOptions(c, name, "")
	case http.MethodGet:
	default:
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	reply, err := h.views.Render(request.Context(), *identity, service.DocumentViewRequest{
		View: view, UID: c.Param("uid"), Path: request.URL.Path, Query: parseReferenceForm(request.URL.RawQuery),
		Group: request.Header.Get("X-Group-Id"), Language: restLanguage(request),
	})
	if err != nil {
		if errors.Is(err, service.ErrDocumentViewNotPorted) {
			c.Logger().Warn(err.Error())
		}
		return messageReadsError(c, err)
	}
	c.Response().Header().Set("Content-Language", reply.Language)
	return c.Blob(reply.Status, "text/html; charset=utf-8", []byte(*reply.HTML))
}
