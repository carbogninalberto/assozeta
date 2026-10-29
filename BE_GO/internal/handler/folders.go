package handler

import (
	"context"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type FolderService interface {
	List(context.Context, domain.ResolvedIdentity, string, bool) (dto.FolderList, error)
}

type FolderDeleteService interface {
	Delete(context.Context, domain.ResolvedIdentity, string) error
}

type FolderMoveService interface {
	Move(context.Context, domain.ResolvedIdentity, string, any) error
}

type FoldersHandler struct {
	folders    FolderService
	deletes    FolderDeleteService
	moves      FolderMoveService
	operations *paymentcategorylifecycle.Lifecycle
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewFoldersHandler(folders FolderService, deletes FolderDeleteService, moves FolderMoveService, actors *service.ActorAuthentication, identities *service.IdentityResolver, operations *paymentcategorylifecycle.Lifecycle) *FoldersHandler {
	return &FoldersHandler{folders: folders, deletes: deletes, moves: moves, actors: actors, identities: identities, operations: operations}
}

// List mirrors FolderViewSet.as_view({'get': 'list'}): HEAD maps to list and
// the viewset's delete() makes DELETE allowed and dispatched without a pk.
func (h *FoldersHandler) List(c echo.Context) error {
	c.Response().Header().Set("Allow", "GET, DELETE, HEAD, OPTIONS")
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, "folders_list")
	if identity == nil {
		return err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return messageReadsOptions(c, "Folder")
	case http.MethodDelete:
		return restError(c, errors.New("folder delete without pk"))
	case http.MethodGet, http.MethodHead:
	default:
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	values := request.URL.Query()["folder"]
	folder := ""
	if len(values) > 0 {
		folder = values[len(values)-1]
	}
	list, err := h.folders.List(request.Context(), *identity, folder, len(values) > 0)
	switch {
	case errors.Is(err, domain.ErrFolderParentNotFound):
		return restError(c, &restInputError{Status: 404, Detail: err.Error()})
	case errors.Is(err, domain.ErrFolderForeign):
		return restError(c, &domain.PermissionDenied{Detail: err.Error()})
	case err != nil:
		return restError(c, err)
	}
	return c.JSON(200, list)
}

func (h *FoldersHandler) Delete(c echo.Context) error {
	c.Response().Header().Set("Allow", "DELETE, OPTIONS")
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodDelete, "folders_delete")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, "Folder")
	}
	if request.Method != http.MethodDelete {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	err = h.deletes.Delete(ctx, *identity, c.Param("pk"))
	switch {
	case errors.Is(err, service.ErrFolderLookup):
		return restError(c, &restInputError{Status: 404, Detail: restText(request, "notfound")})
	case errors.Is(err, domain.ErrFolderNotFound):
		return restError(c, &restInputError{Status: 404, Detail: err.Error()})
	case err != nil:
		return restError(c, err)
	}
	return c.NoContent(204)
}

// Move serves folders/<pk>/move: POST moves; the viewset's delete() makes
// DELETE allowed and dispatched here with the pk, deleting the folder.
func (h *FoldersHandler) Move(c echo.Context) error {
	c.Response().Header().Set("Allow", "POST, DELETE, OPTIONS")
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "folders_move")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return folderMoveOptions(c)
	}
	if request.Method != http.MethodPost && request.Method != http.MethodDelete {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	var body any
	if request.Method == http.MethodPost {
		if body, err = readMessageMutationsBody(c); err != nil {
			return restError(c, err)
		}
	}
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	if request.Method == http.MethodDelete {
		err = h.deletes.Delete(ctx, *identity, c.Param("pk"))
	} else {
		err = h.moves.Move(ctx, *identity, c.Param("pk"), body)
	}
	switch {
	case errors.Is(err, service.ErrFolderLookup):
		return restError(c, &restInputError{Status: 404, Detail: restText(request, "notfound")})
	case errors.Is(err, domain.ErrFolderNotFound):
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

// folderMoveOptions is SimpleMetadata with FolderSerializer's POST fields.
func folderMoveOptions(c echo.Context) error {
	info := func(kind, label string, required, readOnly bool) map[string]any {
		return map[string]any{"type": kind, "required": required, "read_only": readOnly, "label": label}
	}
	name := info("string", "Name", true, false)
	name["max_length"] = 255
	return c.JSON(200, map[string]any{
		"name": "Folder", "description": "", "renders": []string{"application/json"},
		"parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"},
		"actions": map[string]any{"POST": map[string]any{
			"id": info("integer", "ID", false, true), "name": name, "parent": info("field", "Parent", false, false),
			"sport_association": info("field", "Sport association", true, false), "created_at": info("datetime", "Created at", false, false),
			"level": info("integer", "Level", false, true), "children": info("field", "Children", false, true),
		}},
	})
}
