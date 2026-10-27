package handler

import (
	"context"
	"errors"
	"mime"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type DocumentArchiveService interface {
	List(ctx context.Context, identity domain.ResolvedIdentity, folder, page string) (dto.ArchiveList, error)
}

type ArchiveWriteService interface {
	Move(context.Context, domain.ResolvedIdentity, string, any, domain.LoginAuditContext) error
	Delete(context.Context, domain.ResolvedIdentity, string, domain.LoginAuditContext) error
}

type DocumentArchiveHandler struct {
	archive    DocumentArchiveService
	writes     ArchiveWriteService
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewDocumentArchiveHandler(archive DocumentArchiveService, writes ArchiveWriteService, actors *service.ActorAuthentication, identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *DocumentArchiveHandler {
	return &DocumentArchiveHandler{archive: archive, writes: writes, actors: actors, identities: identities, audit: audit, operations: operations}
}

// List mirrors DocumentArchiveViewSet.as_view({'get': 'list'}) with its
// JSONParser-only parser classes and the viewset-wide DELETE dispatch.
func (h *DocumentArchiveHandler) List(c echo.Context) error {
	c.Response().Header().Set("Allow", "GET, DELETE, HEAD, OPTIONS")
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, "documents_list")
	if identity == nil {
		return err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return c.JSON(200, map[string]any{"name": "Document Archive", "description": "",
			"renders": []string{"application/json"}, "parses": []string{"application/json"}})
	case http.MethodDelete:
		return restError(c, errors.New("archive delete without pk"))
	case http.MethodGet, http.MethodHead:
	default:
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	last := func(name, fallback string) string {
		values := request.URL.Query()[name]
		if len(values) == 0 {
			return fallback
		}
		return values[len(values)-1]
	}
	list, err := h.archive.List(request.Context(), *identity, last("folder", ""), last("page", "0"))
	if errors.Is(err, domain.ErrFolderForeign) {
		return restError(c, &domain.PermissionDenied{Detail: err.Error()})
	}
	if err != nil {
		return restError(c, err)
	}
	return c.JSON(200, list)
}

func (h *DocumentArchiveHandler) Move(c echo.Context) error {
	return h.write(c, "POST, DELETE, OPTIONS", http.MethodPost, "documents_move")
}

func (h *DocumentArchiveHandler) Delete(c echo.Context) error {
	return h.write(c, "DELETE, OPTIONS", http.MethodDelete, "documents_delete")
}

// write serves move (POST) and delete; the viewset's delete() makes DELETE
// allowed and dispatched on the move route too. Parsers are JSONParser only.
func (h *DocumentArchiveHandler) write(c echo.Context, allow, method, operation string) error {
	c.Response().Header().Set("Allow", allow)
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, method, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		if method == http.MethodPost {
			return archiveMoveOptions(c)
		}
		return c.JSON(200, map[string]any{"name": "Document Archive", "description": "",
			"renders": []string{"application/json"}, "parses": []string{"application/json"}})
	}
	if request.Method != method && request.Method != http.MethodDelete {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	var body any
	if request.Method == http.MethodPost {
		if media, _, _ := mime.ParseMediaType(request.Header.Get("Content-Type")); request.ContentLength != 0 && media != "application/json" {
			return restError(c, &restInputError{Status: 415, Detail: restText(request, "media")})
		}
		if body, err = readMessageMutationsBody(c); err != nil {
			return restError(c, err)
		}
	}
	audit, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	if request.Method == http.MethodDelete {
		err = h.writes.Delete(ctx, *identity, c.Param("pk"), audit)
	} else {
		err = h.writes.Move(ctx, *identity, c.Param("pk"), body, audit)
	}
	switch {
	case errors.Is(err, service.ErrArchiveLookup):
		return restError(c, &restInputError{Status: 404, Detail: restText(request, "notfound")})
	case errors.Is(err, domain.ErrArchiveNotFound):
		return restError(c, &restInputError{Status: 404, Detail: err.Error()})
	case errors.Is(err, domain.ErrFolderTargetForeign):
		return restError(c, &domain.PermissionDenied{Detail: err.Error()})
	case err != nil:
		return restError(c, err)
	}
	if request.Method == http.MethodDelete {
		return c.NoContent(204)
	}
	return c.NoContent(200)
}

// archiveMoveOptions is SimpleMetadata with DocumentArchiveSerializer's POST fields.
func archiveMoveOptions(c echo.Context) error {
	info := func(kind, label string, required, readOnly bool, extra ...any) map[string]any {
		field := map[string]any{"type": kind, "required": required, "read_only": readOnly, "label": label}
		for i := 0; i+1 < len(extra); i += 2 {
			field[extra[i].(string)] = extra[i+1]
		}
		return field
	}
	document := map[string]any{
		"document_id": info("string", "Document id", false, false), "creation_date": info("datetime", "Creation date", false, false),
		"filepath": info("string", "Filepath", false, false, "max_length", 1000), "filename": info("string", "Filename", true, false, "max_length", 255),
		"file_size_bytes": info("integer", "File size bytes", false, false, "min_value", 0, "max_value", int64(9223372036854775807)),
		"token":           info("string", "Token", false, false),
	}
	child := map[string]any{"type": "nested object", "required": false, "read_only": true, "children": document}
	nested := info("field", "Document", false, true, "child", child)
	return c.JSON(200, map[string]any{
		"name": "Document Archive", "description": "", "renders": []string{"application/json"}, "parses": []string{"application/json"},
		"actions": map[string]any{"POST": map[string]any{
			"sport_association_documents_archive_id": info("string", "Sport association documents archive id", false, false),
			"sport_association":                      info("field", "Sport association", true, false), "document": nested,
			"date": info("date", "Date", false, false), "folder": info("field", "Folder", false, false),
		}},
	})
}
