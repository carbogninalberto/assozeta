package handler

import (
	"errors"
	"mime"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// DocumentRenderHandler serves the PDF function views of
// BE/docmanager/views/printing_views.py (@api_view(['POST', 'GET'])):
// identity resolution, the view's permission classes, method dispatch, then
// the view.
type DocumentRenderHandler struct {
	renders    *service.DocumentRender
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewDocumentRenderHandler(renders *service.DocumentRender, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *DocumentRenderHandler {
	return &DocumentRenderHandler{renders: renders, actors: actors, identities: identities, audit: audit, operations: operations}
}

func (h *DocumentRenderHandler) Subscription(c echo.Context) error {
	return h.serve(c, service.RenderSubscription, "document_subscription", "Document Subscription")
}

func (h *DocumentRenderHandler) Invoice(c echo.Context) error {
	return h.serve(c, service.RenderInvoice, "document_invoice", "Document Invoice")
}

func (h *DocumentRenderHandler) Einvoice(c echo.Context) error {
	return h.serve(c, service.RenderEinvoice, "document_einvoice", "Document Einvoice")
}

func (h *DocumentRenderHandler) Compensation(c echo.Context) error {
	return h.serve(c, service.RenderCompensation, "document_compensation", "Document Compensation")
}

func (h *DocumentRenderHandler) Template(c echo.Context) error {
	return h.serve(c, service.RenderTemplate, "document_template", "Document Template")
}

func (h *DocumentRenderHandler) MedicalAppointment(c echo.Context) error {
	return h.serve(c, service.RenderMedical, "document_medical_appointment", "Document Medical Appointment")
}

func (h *DocumentRenderHandler) serve(c echo.Context, route, operation, name string) error {
	request := c.Request()
	c.Response().Header().Set("Allow", "POST, GET, OPTIONS")
	identity, err := messageMutationsIdentityFor(c, h.actors, h.identities, request.Method, operation, true)
	if identity == nil {
		return err
	}
	query := parseReferenceForm(request.URL.RawQuery)
	authorization := requestAuthorization(request)
	if err := h.renders.Authorize(request.Context(), *identity, route, c.Param("uid"), query, authorization); err != nil {
		if errors.Is(err, service.ErrDocumentRenderUnauthenticated) {
			c.Response().Header().Set("WWW-Authenticate", `Bearer realm="api"`)
			return restError(c, &restInputError{Status: 401, Detail: restText(request, "credentials")})
		}
		return messageReadsError(c, err)
	}
	switch request.Method {
	case http.MethodOptions:
		return supplierOptions(c, name, "")
	case http.MethodGet, http.MethodPost:
	default:
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	audit, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	reply, err := h.renders.Render(ctx, *identity, service.DocumentRenderRequest{
		Route: route, UID: c.Param("uid"), Query: query, Authorization: authorization, Group: request.Header.Get("X-Group-Id"),
		Body: func() (any, bool, error) { return documentRenderBody(c) }, Audit: audit,
	})
	if err != nil {
		var input *restInputError
		if errors.As(err, &input) {
			return restError(c, err)
		}
		return messageReadsError(c, err)
	}
	if reply.PDF != nil {
		c.Response().Header().Set("Content-Disposition", reply.Disposition)
		return c.Blob(reply.Status, "application/pdf", reply.PDF)
	}
	return c.JSON(reply.Status, reply.JSON)
}

// documentRenderBody is request.data for an empty body, JSON or a urlencoded
// form (whether it is a form); other media types are not ported.
func documentRenderBody(c echo.Context) (any, bool, error) {
	request := c.Request()
	if request.ContentLength == 0 {
		return nil, false, nil
	}
	media, _, _ := mime.ParseMediaType(request.Header.Get("Content-Type"))
	switch media {
	case "application/json":
		body, err := readMessageMutationsBody(c)
		return body, false, err
	case "application/x-www-form-urlencoded":
		body, err := readMessageMutationsBody(c)
		return body, true, err
	}
	return nil, false, service.ErrDocumentRenderNotPorted
}
