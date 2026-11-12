package handler

import (
	"context"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/manual"
	"github.com/labstack/echo/v4"
)

type ManualReaderService interface {
	Sections(context.Context, domain.ResolvedIdentity, string) (*dto.ManualSections, error)
	Asset(context.Context, domain.ResolvedIdentity, string) ([]byte, error)
}
type ManualReaderHandler struct {
	reader     ManualReaderService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewManualReaderHandler(reader ManualReaderService, actors *service.ActorAuthentication, identities *service.IdentityResolver) *ManualReaderHandler {
	return &ManualReaderHandler{reader, actors, identities}
}
func (h *ManualReaderHandler) Sections(c echo.Context) error { return h.read(c, false) }
func (h *ManualReaderHandler) Asset(c echo.Context) error    { return h.read(c, true) }
func (h *ManualReaderHandler) read(c echo.Context, asset bool) error {
	operation, name := "manual_sections", "Manual Sections"
	if asset {
		operation, name = "manual_asset", "Manual Asset"
	}
	identity, err := authenticatedRESTIdentity(c, h.actors, h.identities, http.MethodGet, operation)
	if identity == nil {
		return err
	}
	if c.Request().Method == http.MethodOptions {
		return c.JSON(200, map[string]any{"name": name, "description": "", "renders": []string{"application/json"}, "parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}})
	}
	if c.Request().Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(c.Request(), "method")})
	}
	if asset {
		value, err := h.reader.Asset(c.Request().Context(), *identity, c.Param("*"))
		if errors.Is(err, manual.ErrAssetNotFound) {
			return restError(c, &restInputError{Status: 404, Detail: restText(c.Request(), "notfound")})
		}
		if err != nil {
			return restError(c, err)
		}
		c.Response().Header().Set("Cache-Control", "private, no-store")
		c.Response().Header().Set("X-Content-Type-Options", "nosniff")
		return c.Blob(200, "image/png", value)
	}
	rawQuery, _ := parseReferenceForm(c.Request().URL.RawQuery).Lookup("query")
	query, _ := rawQuery.(string)
	value, err := h.reader.Sections(c.Request().Context(), *identity, query)
	if errors.Is(err, service.ErrManualQueryTooLong) {
		return c.JSON(400, map[string]string{"detail": "La ricerca è troppo lunga."})
	}
	if err != nil {
		return restError(c, err)
	}
	return c.JSON(200, value)
}
