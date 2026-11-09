package handler

import (
	"context"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type documentArchiveRouterService interface {
	Create(ctx context.Context, identity domain.ResolvedIdentity, read func() (any, error)) error
	Retrieve(ctx context.Context, identity domain.ResolvedIdentity, raw string) error
}

// RegisterDocuments adds router.register(r'documents',
// DocumentArchiveViewSet) (JSONParser only): list(), the stock create and
// retrieve (both end in a 500 once past validation and lookup), update()
// (partial) for PUT, the stock destroy and the bulk_delete extra action.
func (h *RouterStockHandler) RegisterDocuments(archive *DocumentArchiveHandler, uploads *DocumentUploadsHandler) {
	stock := uploads.uploads.(documentArchiveRouterService)
	h.register(&routerStockViewset{
		prefix: "documents", name: documentArchiveOptionsName, parses: []string{"application/json"}, bulkName: "Bulk delete",
		formatted: map[string]bool{"create": true, "retrieve": true, "destroy": true},
		list: func(c echo.Context, identity *domain.ResolvedIdentity, _ string) error {
			return archive.list(c, identity)
		},
		create: func(c echo.Context, identity *domain.ResolvedIdentity, _ string) error {
			ctx, _, _, release, err := uploads.admit(c, nil)
			if err != nil {
				return err
			}
			defer release()
			return uploads.failure(c, stock.Create(ctx, *identity, jsonOnly(c)))
		},
		retrieve: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) error {
			return uploads.failure(c, stock.Retrieve(c.Request().Context(), *identity, pk))
		},
		update: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) error {
			ctx, _, audit, release, err := uploads.admit(c, nil)
			if err != nil {
				return err
			}
			defer release()
			return uploads.failure(c, uploads.uploads.Update(ctx, *identity, pk, jsonOnly(c), audit))
		},
		destroy: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) error {
			return archive.run(c, identity, false, pk)
		},
		bulkDelete: func(c echo.Context, identity *domain.ResolvedIdentity, _ string) error {
			return archive.run(c, identity, true, "")
		},
		postFields: func(echo.Context, *domain.ResolvedIdentity) (map[string]any, error) { return archiveFields(), nil },
		putFields: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) (map[string]any, error) {
			err := stock.Retrieve(c.Request().Context(), *identity, pk)
			if errors.Is(err, service.ErrArchiveLookup) || errors.Is(err, domain.ErrArchiveNotFound) {
				return nil, nil
			}
			if err != nil && !errors.Is(err, domain.ErrDocumentWritesFailed) {
				return nil, err
			}
			return archiveFields(), nil
		},
	})
}
