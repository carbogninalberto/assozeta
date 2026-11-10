package handler

import (
	"bytes"
	"context"
	"io"
	"mime"
	"mime/multipart"
	"net/http"
	"strings"
	"unicode"

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
	audit      LoginAuditReader
}

func NewAssociationTransferHandler(transfer *service.AssociationTransfer, actors *service.ActorAuthentication,
	identities *service.IdentityResolver, audit LoginAuditReader) *AssociationTransferHandler {
	return &AssociationTransferHandler{transfer: transfer, actors: actors, identities: identities, audit: audit}
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
		audit, err := h.audit(c.Request())
		if err != nil {
			return nil, err
		}
		return h.transfer.Delete(ctx, identity, body, audit)
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
	// The scoped-request guard may parse the body; keep the raw bytes for the upload parser.
	if request := c.Request(); request.Body != nil && request.ContentLength > 0 {
		raw, err := io.ReadAll(request.Body)
		if err != nil {
			return messageReadsError(c, err)
		}
		request.Body = io.NopCloser(bytes.NewReader(raw))
		c.Set(importRawBodyKey, raw)
	}
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

// ImportValidate mirrors validate_import.
func (h *AssociationTransferHandler) ImportValidate(c echo.Context) error {
	return h.importRoute(c, http.MethodPost, "validate_import", func(ctx context.Context) (*service.AssociationTransferReply, error) {
		upload, reply, err := importUpload(c)
		if reply != nil || err != nil {
			return reply, err
		}
		return h.transfer.ImportValidate(ctx, *upload)
	})
}

// ImportStart mirrors start_import.
func (h *AssociationTransferHandler) ImportStart(c echo.Context) error {
	return h.importRoute(c, http.MethodPost, "start_import", func(ctx context.Context) (*service.AssociationTransferReply, error) {
		upload, reply, err := importUpload(c)
		if reply != nil || err != nil {
			return reply, err
		}
		return h.transfer.ImportStart(ctx, *upload)
	})
}

const importRawBodyKey = "association-import-raw-body"

// importUpload is request.FILES.get('file') and
// request.data.get('owner_password', ”) with the multipart and form parsers
// only: a body of another type is 415, no body is 400.
func importUpload(c echo.Context) (*service.AssociationImportUpload, *service.AssociationTransferReply, error) {
	request := c.Request()
	if request.ContentLength <= 0 {
		return &service.AssociationImportUpload{Password: ""}, nil, nil
	}
	media, params, _ := mime.ParseMediaType(request.Header.Get("Content-Type"))
	if media != "multipart/form-data" && media != "application/x-www-form-urlencoded" {
		return nil, nil, &restInputError{Status: 415, Detail: restText(request, "media")}
	}
	if media == "application/x-www-form-urlencoded" {
		body, err := readMessageMutationsBody(c)
		if err != nil {
			return nil, nil, err
		}
		upload := &service.AssociationImportUpload{Password: ""}
		if object, ok := body.(domain.RequestObject); ok {
			if value, present := object.Lookup("owner_password"); present {
				upload.Password = value
			}
		}
		return upload, nil, nil
	}
	raw, _ := c.Get(importRawBodyKey).([]byte)
	return parseImportMultipart(raw, params["boundary"])
}

// parseImportMultipart is Django's MultiPartParser for the import routes:
// the last value per field and per file name, file parts with a sanitized
// filename, request.data's merge of files after fields.
func parseImportMultipart(raw []byte, boundary string) (*service.AssociationImportUpload, *service.AssociationTransferReply, error) {
	if boundary == "" {
		return nil, nil, &restInputError{Status: 400, Detail: "Multipart form parse error - Invalid boundary in multipart: None"}
	}
	fields := map[string]string{}
	files := map[string]*service.AssociationImportFile{}
	reader := multipart.NewReader(bytes.NewReader(raw), boundary)
	for {
		part, err := reader.NextRawPart()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, nil, &restInputError{Status: 400, Detail: "Multipart form parse error - Invalid multipart data"}
		}
		content, err := io.ReadAll(part)
		if err != nil {
			return nil, nil, &restInputError{Status: 400, Detail: "Multipart form parse error - Invalid multipart data"}
		}
		if part.Header.Get("Content-Transfer-Encoding") != "" {
			return nil, nil, domain.ErrAssociationTransferNotPorted
		}
		disposition := part.Header.Get("Content-Disposition")
		if disposition == "" {
			continue
		}
		parameters, ok := djangoHeaderParameters(disposition)
		if !ok {
			return nil, nil, domain.ErrAssociationTransferNotPorted
		}
		name, named := parameters["name"]
		if !named {
			continue
		}
		name = strings.TrimFunc(name, unicode.IsSpace)
		if filename := parameters["filename"]; filename != "" {
			sanitized, ok := djangoUploadName(filename)
			if !ok {
				return nil, nil, domain.ErrAssociationTransferNotPorted
			}
			if sanitized != "" {
				kind := part.Header.Get("Content-Type")
				if index := strings.IndexByte(kind, ';'); index >= 0 {
					kind = kind[:index]
				}
				files[name] = &service.AssociationImportFile{Name: sanitized, ContentType: strings.ToLower(strings.TrimSpace(kind)),
					Content: content}
			}
			continue
		}
		fields[name] = string(content)
	}
	upload := &service.AssociationImportUpload{File: files["file"], Password: ""}
	if file, ok := files["owner_password"]; ok {
		upload.Password = file
	} else if value, ok := fields["owner_password"]; ok {
		upload.Password = value
	}
	return upload, nil, nil
}
