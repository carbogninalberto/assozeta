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

type DocumentArchiveService interface {
	List(ctx context.Context, identity domain.ResolvedIdentity, folder, page string) (dto.ArchiveList, error)
}

type DocumentArchiveHandler struct {
	archive    DocumentArchiveService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewDocumentArchiveHandler(archive DocumentArchiveService, actors *service.ActorAuthentication, identities *service.IdentityResolver) *DocumentArchiveHandler {
	return &DocumentArchiveHandler{archive: archive, actors: actors, identities: identities}
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
