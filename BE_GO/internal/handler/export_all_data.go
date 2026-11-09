package handler

import (
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// ExportAllDataHandler serves export-all-data (@api_view(['POST'])).
type ExportAllDataHandler struct {
	exports    *service.ExportAllData
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewExportAllDataHandler(exports *service.ExportAllData, actors *service.ActorAuthentication,
	identities *service.IdentityResolver) *ExportAllDataHandler {
	return &ExportAllDataHandler{exports: exports, actors: actors, identities: identities}
}

// Export mirrors export_all_data. The view never reads request.data.
func (h *ExportAllDataHandler) Export(c echo.Context) error {
	c.Response().Header().Set("Allow", "POST, OPTIONS")
	request := c.Request()
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, request.Method, "export_all_data")
	if identity == nil {
		return err
	}
	switch request.Method {
	case http.MethodOptions:
		return supplierOptions(c, "Export All Data", "")
	case http.MethodPost:
	default:
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	result, err := h.exports.Export(request.Context(), *identity, request.Header.Get("X-Group-Id"))
	switch {
	case errors.Is(err, service.ErrExportAllDataAthlete):
		return restError(c, &restInputError{Status: http.StatusForbidden, Detail: service.ErrExportAllDataAthlete.Error()})
	case err != nil:
		if !errors.Is(err, domain.ErrExportAllDataServer) && !errors.Is(err, domain.ErrExportAllDataNotPorted) {
			c.Logger().Error(err)
		}
		return messageReadsError(c, err)
	}
	c.Response().Header().Set("Content-Disposition", result.Disposition)
	return c.Blob(http.StatusOK, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", result.Workbook)
}
