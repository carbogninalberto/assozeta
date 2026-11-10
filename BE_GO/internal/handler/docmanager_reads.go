package handler

import (
	"errors"
	"io"
	"net/http"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// DocmanagerReadsHandler serves document/retrieve/<uid> (retrieve_document,
// @api_view(['GET']), AllowAny) and document/<uid>/delete
// (application.document_scope.delete_document, @api_view(['DELETE']),
// IsAuthenticated).
type DocmanagerReadsHandler struct {
	documents  *service.DocmanagerReads
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

// WithWrites installs the audit reader and the operation tracker the delete
// route writes under.
func (h *DocmanagerReadsHandler) WithWrites(audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *DocmanagerReadsHandler {
	h.audit, h.operations = audit, operations
	return h
}

func NewDocmanagerReadsHandler(documents *service.DocmanagerReads, actors *service.ActorAuthentication,
	identities *service.IdentityResolver) *DocmanagerReadsHandler {
	return &DocmanagerReadsHandler{documents: documents, actors: actors, identities: identities}
}

func docmanagerRespond(c echo.Context, reply *service.DocmanagerReply, err error) error {
	var invalid *service.PaymentInfoUUIDError
	switch {
	case errors.As(err, &invalid):
		// is_valid_uuid raises ValidationError(ValueError): a one-item list.
		return c.JSON(400, []string{invalid.Detail})
	case err != nil:
		// Unhandled view exceptions and refused (not ported) paths are the 500 page.
		return restError(c, err)
	case reply.Stream == nil:
		return c.JSON(reply.Status, reply.Body)
	}
	stream := reply.Stream
	defer stream.Object.Body.Close()
	header := c.Response().Header()
	header.Set("Content-Type", stream.ContentType)
	if stream.ContentLength != nil {
		header.Set("Content-Length", strconv.FormatInt(*stream.ContentLength, 10))
	}
	if stream.ContentEncoding != "" {
		header.Set("Content-Encoding", stream.ContentEncoding)
	}
	header.Set("X-Content-Type-Options", "nosniff")
	if stream.Disposition != nil {
		header.Set("Content-Disposition", *stream.Disposition)
	}
	c.Response().WriteHeader(reply.Status)
	// Headers are sent: a provider failure now ends the connection early, as
	// Django's StreamingHttpResponse does.
	_, err = io.Copy(c.Response(), stream.Object.Body)
	return err
}

// Retrieve mirrors retrieve_document.
func (h *DocmanagerReadsHandler) Retrieve(c echo.Context) error {
	identity, err := messageMutationsIdentityFor(c, h.actors, h.identities, http.MethodGet, "retrieve_document", true)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Retrieve Document", "")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	var authorization *string
	if values := request.Header.Values("Authorization"); len(values) > 0 {
		// Django joins repeated headers with a comma.
		joined := strings.Join(values, ",")
		authorization = &joined
	}
	reply, err := h.documents.Retrieve(request.Context(), service.RetrieveRequest{
		UID: c.Param("uid"), Query: parseReferenceForm(request.URL.RawQuery), Cookie: strings.Join(request.Header.Values("Cookie"), "; "),
		Group: request.Header.Get("X-Group-Id"), Identity: *identity, Authorization: authorization,
	})
	return docmanagerRespond(c, reply, err)
}

// Delete mirrors delete_document. The view never reads the request body.
func (h *DocmanagerReadsHandler) Delete(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodDelete, "delete_document")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Delete Document", "")
	}
	if request.Method != http.MethodDelete {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	if h.audit == nil || h.operations == nil {
		return docmanagerRespond(c, nil, domain.ErrDocmanagerNotPorted)
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	auditContext, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	reply, err := h.documents.Delete(ctx, *identity, c.Param("uid"), auditContext, request.Header.Get("X-Group-Id"))
	return docmanagerRespond(c, reply, err)
}
