package handler

import (
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"net/http"

	"github.com/labstack/echo/v4"
)

type HealthHandler struct {
	service *service.HealthService
	version string
}

func NewHealthHandler(s *service.HealthService, version string) *HealthHandler {
	return &HealthHandler{service: s, version: version}
}
func getOnly(c echo.Context) bool {
	if c.Request().Method == http.MethodGet {
		return true
	}
	c.Response().Header().Set("Allow", "GET")
	return false
}
func (h *HealthHandler) Health(c echo.Context) error {
	if !getOnly(c) {
		return c.NoContent(http.StatusMethodNotAllowed)
	}
	return c.JSON(http.StatusOK, dto.HealthResponse{Status: "ok", Version: h.version})
}
func (h *HealthHandler) Readiness(c echo.Context) error {
	if !getOnly(c) {
		return c.NoContent(http.StatusMethodNotAllowed)
	}
	result := h.service.Readiness(c.Request().Context())
	status := http.StatusOK
	if !result.Ready() {
		status = http.StatusServiceUnavailable
	}
	return c.JSON(status, result)
}
