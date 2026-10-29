package handler

import (
	"context"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/labstack/echo/v4"
)

type InstanceManifestReader interface {
	Read(context.Context) (*dto.InstanceManifest, error)
}

type InstanceManifestHandler struct{ reader InstanceManifestReader }

func NewInstanceManifestHandler(reader InstanceManifestReader) *InstanceManifestHandler {
	return &InstanceManifestHandler{reader: reader}
}

func (h *InstanceManifestHandler) Read(c echo.Context) error {
	r := c.Request()
	c.Response().Header().Set("Allow", "GET, HEAD, OPTIONS")
	c.Response().Header().Set("Content-Language", handlerReadRouteLocale.Language(r))
	query := parseReferenceForm(r.URL.RawQuery)
	if format, ok := query.Lookup("format"); ok && format != "" && format != "json" {
		return restError(c, &restInputError{Status: 404, Detail: handlerReadRouteLocale.Text(r, readRouteNotFound)})
	}
	if !acceptsJSON(r.Header.Get("Accept")) {
		return restError(c, &restInputError{Status: 406, Detail: handlerReadRouteLocale.Text(r, readRouteAccept)})
	}
	// The Django view explicitly disables authentication. Neither Bearer nor
	// impersonation/group headers select a different public manifest.
	if r.Method == http.MethodOptions {
		return c.JSON(200, map[string]any{"name": "Instance Manifest", "description": "GET /instance/manifest.json\nDynamic PWA manifest.",
			"renders": []string{"application/json"}, "parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}})
	}
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		return restError(c, &restInputError{Status: 405, Detail: handlerReadRouteLocale.Text(r, readRouteMethod)})
	}
	value, err := h.reader.Read(r.Context())
	if err != nil {
		return restError(c, err)
	}
	return c.JSON(200, value)
}
