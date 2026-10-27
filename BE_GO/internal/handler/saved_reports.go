package handler

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type SavedReportService interface {
	List(context.Context, domain.ResolvedIdentity) ([]dto.SavedReportSummary, error)
	Info(context.Context, domain.ResolvedIdentity, string) (dto.SavedReportDetail, error)
	Add(context.Context, domain.ResolvedIdentity, any) (string, string, error)
	Update(context.Context, domain.ResolvedIdentity, string, any) (string, json.RawMessage, error)
	Delete(context.Context, domain.ResolvedIdentity, string) error
}

type SavedReportsHandler struct {
	reports    SavedReportService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	operations *paymentcategorylifecycle.Lifecycle
}

func NewSavedReportsHandler(reports SavedReportService, actors *service.ActorAuthentication, identities *service.IdentityResolver, operations *paymentcategorylifecycle.Lifecycle) *SavedReportsHandler {
	return &SavedReportsHandler{reports: reports, actors: actors, identities: identities, operations: operations}
}

func (h *SavedReportsHandler) List(c echo.Context) error {
	return h.serve(c, "list", http.MethodGet, "GET, HEAD, DELETE, OPTIONS")
}
func (h *SavedReportsHandler) Add(c echo.Context) error {
	return h.serve(c, "add", http.MethodPost, "POST, DELETE, OPTIONS")
}
func (h *SavedReportsHandler) Info(c echo.Context) error {
	return h.serve(c, "info", http.MethodGet, "GET, HEAD, DELETE, OPTIONS")
}
func (h *SavedReportsHandler) Update(c echo.Context) error {
	return h.serve(c, "update", http.MethodPatch, "PATCH, DELETE, OPTIONS")
}
func (h *SavedReportsHandler) Delete(c echo.Context) error {
	return h.serve(c, "delete", http.MethodDelete, "DELETE, OPTIONS")
}

// serve mirrors SavedReportViewSet.as_view(...): the viewset's delete()
// answers DELETE on every route with that route's pk (none on list/add).
func (h *SavedReportsHandler) serve(c echo.Context, action, method, allow string) error {
	c.Response().Header().Set("Allow", allow)
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, method, "saved_report_"+action)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return messageReadsOptions(c, "Saved Report")
	}
	if request.Method == http.MethodHead && method == http.MethodGet {
		request.Method = http.MethodGet
	}
	if request.Method != method && request.Method != http.MethodDelete {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	var body any
	if request.Method == http.MethodPost || request.Method == http.MethodPatch {
		if body, err = readMessageMutationsBody(c); err != nil {
			return restError(c, err)
		}
	}
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	pk := c.Param("pk")
	var result any
	status := 200
	switch {
	case request.Method == http.MethodDelete:
		err, status = h.reports.Delete(ctx, *identity, pk), 204
	case action == "list":
		var rows []dto.SavedReportSummary
		if rows, err = h.reports.List(ctx, *identity); err == nil {
			result = map[string]any{"data": rows}
		}
	case action == "info":
		var row dto.SavedReportDetail
		if row, err = h.reports.Info(ctx, *identity, pk); err == nil {
			result = map[string]any{"data": row}
		}
	case action == "add":
		var id, name string
		if id, name, err = h.reports.Add(ctx, *identity, body); err == nil {
			result, status = map[string]string{"saved_report_id": id, "name": name}, 201
		}
	default:
		var id string
		var name json.RawMessage
		if id, name, err = h.reports.Update(ctx, *identity, pk, body); err == nil {
			result = map[string]any{"saved_report_id": id, "name": name}
		}
	}
	var unknown *service.SavedReportUnknownTool
	switch {
	case errors.Is(err, domain.ErrSavedReportNotFound):
		return c.JSON(404, map[string]string{"error": err.Error()})
	case errors.Is(err, service.ErrSavedReportRequired), errors.As(err, &unknown):
		return c.JSON(400, map[string]string{"error": err.Error()})
	case err != nil:
		return restError(c, err)
	}
	if status == 204 {
		return c.NoContent(204)
	}
	return c.JSON(status, result)
}
