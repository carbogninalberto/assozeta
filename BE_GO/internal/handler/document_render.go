package handler

import (
	"errors"
	"mime"
	"net/http"
	"strconv"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
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
	// Kept in memory so identity resolution and the view read the same bytes.
	multipart, err := bufferMultipart(c)
	if err != nil {
		return messageReadsError(c, err)
	}
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
		Body: func() (any, bool, error) { return documentRenderBody(c, multipart) }, Audit: audit,
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
		// CommonMiddleware's Content-Length: a filled form is larger than the buffer net/http measures by itself.
		c.Response().Header().Set("Content-Length", strconv.Itoa(len(reply.PDF)))
		return c.Blob(reply.Status, "application/pdf", reply.PDF)
	}
	return c.JSON(reply.Status, reply.JSON)
}

// documentRenderBody is request.data for the default parsers (whether it is a
// form): an empty dict without a body, JSON, a urlencoded form or multipart
// (raw: the buffered multipart body). What the parsers raise is a
// service.DocumentRenderBodyError wrapping the DRF answer.
func documentRenderBody(c echo.Context, raw []byte) (any, bool, error) {
	request := c.Request()
	if request.ContentLength == 0 {
		return domain.RequestObject{}, false, nil
	}
	wrap := func(body any, form bool, err error) (any, bool, error) {
		var input *restInputError
		if errors.As(err, &input) {
			return nil, false, &service.DocumentRenderBodyError{Detail: input.Detail, Err: err}
		}
		return body, form, err
	}
	media, params, _ := mime.ParseMediaType(request.Header.Get("Content-Type"))
	switch media {
	case "application/json":
		body, err := readMessageMutationsBody(c)
		return wrap(body, false, err)
	case "application/x-www-form-urlencoded":
		body, err := readMessageMutationsBody(c)
		return wrap(body, true, err)
	case "multipart/form-data":
		if raw == nil {
			return nil, false, service.ErrDocumentRenderNotPorted
		}
		body, err := parseDjangoMultipart(raw, params["boundary"])
		return wrap(body, true, err)
	}
	return wrap(nil, false, &restInputError{Status: 415, Detail: restText(request, "media")})
}
