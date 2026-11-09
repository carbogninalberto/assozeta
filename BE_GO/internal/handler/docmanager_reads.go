package handler

import (
	"errors"
	"io"
	"net/http"
	"strconv"
	"strings"

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
	reply, err := h.documents.Retrieve(request.Context(), service.RetrieveRequest{
		UID: c.Param("uid"), Query: parseReferenceForm(request.URL.RawQuery), Cookie: strings.Join(request.Header.Values("Cookie"), "; "),
		Group: request.Header.Get("X-Group-Id"), Identity: *identity,
	})
	return docmanagerRespond(c, reply, err)
}

// Delete mirrors delete_document up to the ownership walk.
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
	reply, err := h.documents.Delete(request.Context(), *identity, c.Param("uid"))
	return docmanagerRespond(c, reply, err)
}
