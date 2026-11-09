package handler

import (
	"context"
	"mime"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// AssociationTransferHandler serves association/export/{active,list,status,
// delete,start} (AssociationExportViewSet) and association/import/{status,
// validate,start} (AssociationImportViewSet).
type AssociationTransferHandler struct {
	transfer   *service.AssociationTransfer
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewAssociationTransferHandler(transfer *service.AssociationTransfer, actors *service.ActorAuthentication,
	identities *service.IdentityResolver) *AssociationTransferHandler {
	return &AssociationTransferHandler{transfer: transfer, actors: actors, identities: identities}
}

const (
	associationExportDescription = "API endpoints for association data export.\n\n" +
		"Allows association owners to export their data for migration\nto a self-hosted instance."
	associationImportDescription = "API endpoints for association data import.\n\n" +
		"Allows importing association data from a ZIP export file\ninto a fresh Bakney instance."
)

func associationTransferReply(c echo.Context, reply *service.AssociationTransferReply, err error) error {
	if err != nil {
		return messageReadsError(c, err)
	}
	return c.JSON(reply.Status, reply.Body)
}

// viewsetMethod is the request method a ViewSet route dispatches: a route
// mapping 'get' also maps 'head'.
func viewsetMethod(request *http.Request, verb string) string {
	if verb == http.MethodGet && request.Method == http.MethodHead {
		return http.MethodGet
	}
	return request.Method
}

func viewsetAllow(c echo.Context, verb string) {
	if verb == http.MethodGet {
		c.Response().Header().Set("Allow", "GET, HEAD, OPTIONS")
	} else {
		c.Response().Header().Set("Allow", verb+", OPTIONS")
	}
}

func (h *AssociationTransferHandler) export(c echo.Context, verb, operation string,
	run func(ctx context.Context, identity domain.ResolvedIdentity) (*service.AssociationTransferReply, error)) error {
	viewsetAllow(c, verb)
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, verb, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Association Export", associationExportDescription)
	}
	if viewsetMethod(request, verb) != verb {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	reply, err := run(request.Context(), *identity)
	return associationTransferReply(c, reply, err)
}

func queryTask(request *http.Request) string {
	value, _ := parseReferenceForm(request.URL.RawQuery).Lookup("task_id")
	task, _ := value.(string)
	return task
}

// ExportActive mirrors active_export.
func (h *AssociationTransferHandler) ExportActive(c echo.Context) error {
	return h.export(c, http.MethodGet, "active_export", h.transfer.Active)
}

// ExportList mirrors list_exports.
func (h *AssociationTransferHandler) ExportList(c echo.Context) error {
	return h.export(c, http.MethodGet, "list_exports", h.transfer.List)
}

// ExportStatus mirrors export_status.
func (h *AssociationTransferHandler) ExportStatus(c echo.Context) error {
	return h.export(c, http.MethodGet, "export_status", func(ctx context.Context, identity domain.ResolvedIdentity) (*service.AssociationTransferReply, error) {
		return h.transfer.Status(ctx, identity, queryTask(c.Request()))
	})
}

// ExportDelete mirrors delete_export.
func (h *AssociationTransferHandler) ExportDelete(c echo.Context) error {
	return h.export(c, http.MethodDelete, "delete_export", func(ctx context.Context, identity domain.ResolvedIdentity) (*service.AssociationTransferReply, error) {
		body, err := readMessageMutationsBody(c)
		if err != nil {
			return nil, err
		}
		return h.transfer.Delete(ctx, identity, body)
	})
}

// ExportStart mirrors start_export up to the Celery dispatch.
func (h *AssociationTransferHandler) ExportStart(c echo.Context) error {
	return h.export(c, http.MethodPost, "start_export", h.transfer.Start)
}

// importRoute runs SetupTokenOrAuthenticated before OPTIONS and method
// dispatch (DRF checks permissions first).
func (h *AssociationTransferHandler) importRoute(c echo.Context, verb, operation string, run func(ctx context.Context) (*service.AssociationTransferReply, error)) error {
	viewsetAllow(c, verb)
	identity, err := messageMutationsIdentityFor(c, h.actors, h.identities, verb, operation, true)
	if identity == nil {
		return err
	}
	request := c.Request()
	allowed, err := h.transfer.ImportAllowed(request.Context(), *identity, request.Header.Get("X-Setup-Token"))
	if err != nil {
		return messageReadsError(c, err)
	}
	if !allowed {
		if identity.ScopeUser == nil {
			c.Response().Header().Set("WWW-Authenticate", `Bearer realm="api"`)
			return restError(c, &restInputError{Status: 401, Detail: restText(request, "credentials")})
		}
		return c.JSON(403, map[string]string{"detail": "Setup token missing or invalid."})
	}
	if request.Method == http.MethodOptions {
		return c.JSON(200, map[string]any{"name": "Association Import", "description": associationImportDescription,
			"renders": []string{"application/json"}, "parses": []string{"multipart/form-data", "application/x-www-form-urlencoded"}})
	}
	if viewsetMethod(request, verb) != verb {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	reply, err := run(request.Context())
	return associationTransferReply(c, reply, err)
}

// ImportStatus mirrors import_status.
func (h *AssociationTransferHandler) ImportStatus(c echo.Context) error {
	return h.importRoute(c, http.MethodGet, "import_status", func(ctx context.Context) (*service.AssociationTransferReply, error) {
		return h.transfer.ImportStatus(ctx, queryTask(c.Request()))
	})
}

// ImportValidate mirrors validate_import; parsing an uploaded archive is not ported.
func (h *AssociationTransferHandler) ImportValidate(c echo.Context) error {
	return h.importRoute(c, http.MethodPost, "validate_import", func(context.Context) (*service.AssociationTransferReply, error) {
		return importUpload(c)
	})
}

// ImportStart mirrors start_import; storing the archive and dispatching the
// import task are not ported.
func (h *AssociationTransferHandler) ImportStart(c echo.Context) error {
	return h.importRoute(c, http.MethodPost, "start_import", func(context.Context) (*service.AssociationTransferReply, error) {
		return importUpload(c)
	})
}

// importUpload is request.FILES.get('file') with the multipart and form
// parsers only: a body of another type is 415, no body or no file is 400.
func importUpload(c echo.Context) (*service.AssociationTransferReply, error) {
	request := c.Request()
	if request.ContentLength <= 0 {
		return &service.AssociationTransferReply{Status: 400, Body: map[string]string{"error": "File ZIP richiesto"}}, nil
	}
	media, _, _ := mime.ParseMediaType(request.Header.Get("Content-Type"))
	if media != "multipart/form-data" && media != "application/x-www-form-urlencoded" {
		return nil, &restInputError{Status: 415, Detail: restText(request, "media")}
	}
	body, err := readMessageMutationsBody(c)
	if err != nil {
		return nil, err
	}
	object, _ := body.(domain.RequestObject)
	if file, ok := object.Lookup("file"); ok {
		if _, upload := file.(domain.RequestUpload); upload {
			return nil, domain.ErrAssociationTransferNotPorted
		}
	}
	return &service.AssociationTransferReply{Status: 400, Body: map[string]string{"error": "File ZIP richiesto"}}, nil
}
