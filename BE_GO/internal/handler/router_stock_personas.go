package handler

import (
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// RegisterPersonas adds router.register(r'personas', AssociateViewSet):
// list(), the stock create, retrieve (get_object() and AssociateSerializer,
// as info), update() (partial) for PUT and the stock destroy, which is
// Associate.delete(): a hard delete through the Collector (the viewset's own
// delete() soft-deletes instead).
func (h *RouterStockHandler) RegisterPersonas(reads *PersonasHandler, writes *PersonasWritesHandler, destroy *service.RouterStockDestroy) {
	read := func(c echo.Context) (domain.RequestObject, string) {
		request := c.Request()
		return parseReferenceForm(request.URL.RawQuery), request.Header.Get("X-Group-Id")
	}
	render := func(c echo.Context, status int, body any, err error) error {
		if err != nil {
			return personaWriteError(c, err)
		}
		return c.Blob(status, echo.MIMEApplicationJSON, dto.PythonJSONDumpsCompact(body))
	}
	h.register(&routerStockViewset{
		prefix: "personas", name: "Associate",
		formatted: map[string]bool{"create": true, "retrieve": true, "destroy": true},
		list: func(c echo.Context, identity *domain.ResolvedIdentity, _ string) error {
			query, group := read(c)
			body, err := reads.personas.List(c.Request().Context(), *identity, query, group)
			return render(c, http.StatusOK, body, err)
		},
		listDelete: func(c echo.Context, identity *domain.ResolvedIdentity, _ string) error {
			return writes.deleteWithoutPK(c, *identity)
		},
		create: func(c echo.Context, identity *domain.ResolvedIdentity, _ string) error {
			write, release, err := admitRESTWrite(c, writes.operations, writes.audit)
			if write == nil {
				return err
			}
			defer release()
			header := c.Request().Header.Get("X-Group-Id")
			id, err := writes.writes.StockCreate(write.Context, *identity, write.Body, write.Form, header, write.Audit)
			if err != nil {
				return personaWriteError(c, err)
			}
			body, err := writes.personas.Render(write.Context, id, header)
			return render(c, http.StatusCreated, body, err)
		},
		retrieve: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) error {
			query, group := read(c)
			body, err := reads.personas.Info(c.Request().Context(), *identity, pk, query, group)
			return render(c, http.StatusOK, body, err)
		},
		update: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) error {
			query, header := read(c)
			row, err := writes.writes.Locate(c.Request().Context(), *identity, pk, query, header)
			if err != nil {
				return personaWriteError(c, err)
			}
			write, release, err := admitRESTWrite(c, writes.operations, writes.audit)
			if write == nil {
				return err
			}
			defer release()
			if err = writes.writes.Update(write.Context, *identity, row, write.Body, write.Form, header, write.Audit); err != nil {
				return personaWriteError(c, err)
			}
			body, err := writes.personas.Info(write.Context, *identity, pk, query, header)
			return render(c, http.StatusOK, body, err)
		},
		destroy: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) error {
			query, header := read(c)
			row, err := writes.writes.Locate(c.Request().Context(), *identity, pk, query, header)
			if err != nil {
				return personaWriteError(c, err)
			}
			write, release, err := admitRESTWrite(c, writes.operations, writes.audit)
			if write == nil {
				return err
			}
			defer release()
			if err = destroy.Delete(write.Context, *identity, "application.Associate", row.ID, header, write.Audit); err != nil {
				return personaWriteError(c, err)
			}
			return c.NoContent(http.StatusNoContent)
		},
		postFields: func(echo.Context, *domain.ResolvedIdentity) (map[string]any, error) { return personaOptionsPost()["POST"].(map[string]any), nil },
		putFields: func(c echo.Context, identity *domain.ResolvedIdentity, pk string) (map[string]any, error) {
			query, header := read(c)
			_, err := writes.writes.Locate(c.Request().Context(), *identity, pk, query, header)
			switch {
			case errors.Is(err, service.ErrPersonaNotSportAssociation), errors.Is(err, service.ErrPersonaInvalidPK),
				errors.Is(err, domain.ErrPersonaNotFound):
				return nil, nil
			case err != nil:
				return nil, err
			}
			return personaOptionsPost()["POST"].(map[string]any), nil
		},
	})
}
