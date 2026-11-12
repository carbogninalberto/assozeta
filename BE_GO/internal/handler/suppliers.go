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

type SupplierService interface {
	List(context.Context, domain.ResolvedIdentity, service.SupplierListRequest) (map[string]any, error)
	Info(context.Context, domain.ResolvedIdentity, string, string) (map[string]any, error)
}

type SuppliersHandler struct {
	suppliers  SupplierService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
}

func NewSuppliersHandler(suppliers SupplierService, actors *service.ActorAuthentication, identities *service.IdentityResolver) *SuppliersHandler {
	return &SuppliersHandler{suppliers: suppliers, actors: actors, identities: identities}
}

func supplierOptions(c echo.Context, name, description string) error {
	return c.JSON(200, map[string]any{"name": name, "description": description, "renders": []string{"application/json"},
		"parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}})
}

func (h *SuppliersHandler) answer(c echo.Context, body map[string]any, err error) error {
	switch {
	case errors.Is(err, service.ErrSupplierForbidden):
		return c.NoContent(403)
	case errors.Is(err, dto.ErrDRFInvalidPage):
		return c.JSON(404, map[string]string{"detail": "Invalid page."})
	case errors.Is(err, domain.ErrSupplierNotFound):
		return c.JSON(404, map[string]string{"msg": "Supplier not found."})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, body)
}

func (h *SuppliersHandler) List(c echo.Context) error {
	identity, err := messageReadsIdentity(c, h.actors, h.identities, http.MethodGet, "supplier_list")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Supplier List", "API endpoint to return a list of suppliers")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	body, err := h.suppliers.List(request.Context(), *identity, service.SupplierListRequest{
		Query: parseReferenceForm(request.URL.RawQuery), GroupHeader: request.Header.Get("X-Group-Id")})
	return h.answer(c, body, err)
}

func (h *SuppliersHandler) Info(c echo.Context) error {
	identity, err := messageReadsIdentity(c, h.actors, h.identities, http.MethodGet, "supplier_info")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Supplier Info", "API endpoint to get detailed information about a specific supplier")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	body, err := h.suppliers.Info(request.Context(), *identity, c.Param("uid"), request.Header.Get("X-Group-Id"))
	return h.answer(c, body, err)
}
