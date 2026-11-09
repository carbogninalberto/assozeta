package handler

import (
	"context"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type moduleTemplateRetriever interface {
	Retrieve(ctx context.Context, identity domain.ResolvedIdentity, raw string) (dto.ModuleTemplateRow, error)
}

// RegisterTemplates adds router.register(r'templates',
// SportAssociationModuleTemplatesViewSet): its own list() and create() (the
// add route), update() (partial) for PUT, the stock retrieve and destroy.
func (h *RouterStockHandler) RegisterTemplates(templates *ModuleTemplatesHandler) {
	retriever := templates.templates.(moduleTemplateRetriever)
	run := func(action string) routerStockAction {
		return func(c echo.Context, identity *domain.ResolvedIdentity, pk string) error {
			return templates.run(c, identity, action, pk)
		}
	}
	retrieve := func(c echo.Context, identity *domain.ResolvedIdentity, pk string) (dto.ModuleTemplateRow, error) {
		return retriever.Retrieve(c.Request().Context(), *identity, pk)
	}
	h.register(&routerStockViewset{
		prefix: "templates", name: moduleTemplatesView,
		formatted: map[string]bool{"retrieve": true, "destroy": true},
		list:      run("list"), create: run("add"), update: run("update"), destroy: run("delete"),
		retrieve: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) error {
			row, err := retrieve(c, identity, pk)
			switch {
			case errors.Is(err, service.ErrModuleTemplateLookup):
				return restError(c, &restInputError{Status: 404, Detail: restText(c.Request(), "notfound")})
			case errors.Is(err, domain.ErrModuleTemplateNotFound):
				return restError(c, &restInputError{Status: 404, Detail: err.Error()})
			case err != nil:
				return messageReadsError(c, err)
			}
			return c.JSON(200, row)
		},
		postFields: func(echo.Context, *domain.ResolvedIdentity) (map[string]any, error) {
			return moduleTemplatesFields(), nil
		},
		putFields: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) (map[string]any, error) {
			_, err := retrieve(c, identity, pk)
			if errors.Is(err, service.ErrModuleTemplateLookup) || errors.Is(err, domain.ErrModuleTemplateNotFound) {
				return nil, nil
			}
			if err != nil {
				return nil, err
			}
			return moduleTemplatesFields(), nil
		},
	})
}
