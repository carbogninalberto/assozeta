package handler

import (
	"context"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type folderRouterService interface {
	Create(ctx context.Context, identity domain.ResolvedIdentity, body service.FolderBody) (dto.FolderNode, error)
	Retrieve(ctx context.Context, identity domain.ResolvedIdentity, raw string) (dto.FolderNode, error)
}

// RegisterFolders adds router.register(r'folders', FolderViewSet): list(),
// the stock create, retrieve and destroy (the viewset's delete() does the
// same), update() (partial) for PUT.
func (h *RouterStockHandler) RegisterFolders(folders *FoldersHandler, writes *FolderWritesHandler) {
	stock := writes.writes.(folderRouterService)
	h.register(&routerStockViewset{
		prefix: "folders", name: "Folder",
		formatted: map[string]bool{"create": true, "retrieve": true, "destroy": true},
		list: func(c echo.Context, identity *domain.ResolvedIdentity, _ string) error {
			return folders.list(c, identity)
		},
		create: func(c echo.Context, identity *domain.ResolvedIdentity, _ string) error {
			return writes.write(c, func(ctx context.Context, body service.FolderBody) (int, dto.FolderNode, error) {
				folder, err := stock.Create(ctx, *identity, body)
				return http.StatusCreated, folder, err
			})
		},
		retrieve: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) error {
			folder, err := stock.Retrieve(c.Request().Context(), *identity, pk)
			switch {
			case errors.Is(err, service.ErrFolderLookup):
				return restError(c, &restInputError{Status: 404, Detail: restText(c.Request(), "notfound")})
			case errors.Is(err, domain.ErrFolderNotFound):
				return restError(c, &restInputError{Status: 404, Detail: err.Error()})
			case err != nil:
				return restError(c, err)
			}
			return c.JSON(http.StatusOK, folder)
		},
		update: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) error {
			return writes.write(c, func(ctx context.Context, body service.FolderBody) (int, dto.FolderNode, error) {
				folder, err := writes.writes.Update(ctx, *identity, pk, body)
				return http.StatusOK, folder, err
			})
		},
		destroy: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) error {
			return folders.destroy(c, identity, pk)
		},
		postFields: func(echo.Context, *domain.ResolvedIdentity) (map[string]any, error) { return folderFields(), nil },
		putFields: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) (map[string]any, error) {
			_, err := stock.Retrieve(c.Request().Context(), *identity, pk)
			if errors.Is(err, service.ErrFolderLookup) || errors.Is(err, domain.ErrFolderNotFound) {
				return nil, nil
			}
			if err != nil {
				return nil, err
			}
			return folderFields(), nil
		},
	})
}
