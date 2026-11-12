package handler

import (
	"context"
	"errors"
	"net/http"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type CampsPeriodService interface {
	AddPeriod(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool, audit domain.LoginAuditContext) (*service.CampsPeriodResult, error)
	PeriodInfo(ctx context.Context, identity domain.ResolvedIdentity, raw string) (*service.CampsPeriodInfo, error)
	UpdatePeriod(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, form bool, audit domain.LoginAuditContext) (*service.CampsPeriodResult, error)
	DeletePeriod(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error
	AddService(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool, audit domain.LoginAuditContext) (*domain.CampsService, string, error)
	UpdateService(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, form bool, group string, audit domain.LoginAuditContext) (*domain.CampsService, error)
	DeleteService(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error
}

type CampsPeriodsHandler struct {
	camps      CampsPeriodService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewCampsPeriodsHandler(camps CampsPeriodService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *CampsPeriodsHandler {
	return &CampsPeriodsHandler{camps: camps, actors: actors, identities: identities, audit: audit, operations: operations}
}

// campsErrorsRepr is str(ValidationError(serializer.errors)): the dict repr
// of ErrorDetail lists.
func campsErrorsRepr(request *http.Request, invalid *service.CampsValidationError) string {
	english := restLanguage(request) == "en"
	var out strings.Builder
	out.WriteString("{")
	for index, field := range invalid.Fields {
		if index > 0 {
			out.WriteString(", ")
		}
		out.WriteString(dto.PythonStringRepr(field.Name) + ": [")
		for position, code := range field.Codes {
			if position > 0 {
				out.WriteString(", ")
			}
			out.WriteString("ErrorDetail(string=" + dto.PythonStringRepr(campsValidationMessage(code, english)) +
				", code=" + dto.PythonStringRepr(dto.CampsDRFCode(code)) + ")")
		}
		out.WriteString("]")
	}
	out.WriteString("}")
	return out.String()
}

func campsValidationMessage(code string, english bool) string {
	if limit, ok := strings.CutPrefix(code, dto.CampsValidationMaxValue); ok {
		if english {
			return "Ensure this value is less than or equal to " + limit + "."
		}
		return "Assicurati che il valore sia minore o uguale a " + limit + "."
	}
	if code == dto.CampsValidationNoData {
		return "No data provided"
	}
	return paymentCategoryValidationMessage(code, english)
}

// campsError maps the views' answers; add views turn every exception,
// including the tenant scope's PermissionDenied, into 500 {'error': str(e)}.
func campsError(c echo.Context, err error, add bool, notAssociation string) error {
	request := c.Request()
	var invalid *service.CampsValidationError
	var python *dto.CampsPythonError
	var failure *service.CampsSaveFailure
	var forbidden *service.CampsForbidden
	switch {
	case errors.Is(err, service.ErrCampsNotAssociation):
		return c.JSON(403, map[string]string{"error": notAssociation})
	case errors.As(err, &invalid):
		return c.JSON(400, map[string]string{"error": campsErrorsRepr(request, invalid)})
	case errors.Is(err, service.ErrCampsNotFound):
		return c.JSON(404, map[string]string{"error": "Not found"})
	case errors.As(err, &forbidden):
		return restError(c, &domain.PermissionDenied{Detail: forbidden.Detail})
	case errors.Is(err, service.ErrCampsTenant) && add:
		return c.JSON(500, map[string]string{"error": messageReadsPermissionDetail(request)})
	case errors.Is(err, service.ErrCampsTenant):
		return restError(c, &domain.PermissionDenied{Detail: messageReadsPermissionDetail(request)})
	case errors.As(err, &python):
		return c.JSON(500, map[string]string{"error": python.Message})
	case errors.As(err, &failure):
		return c.JSON(500, map[string]string{"error": failure.Err.Message})
	}
	// Malformed uids (400 list) and the refused, not ported inputs (500 page).
	return messageReadsError(c, err)
}

func (h *CampsPeriodsHandler) serve(c echo.Context, method, operation, name, description string,
	run func(ctx context.Context, identity domain.ResolvedIdentity, write *restWrite) error) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, method, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, name, description)
	}
	// Django's View answers HEAD with the view's get().
	if request.Method != method && !(request.Method == http.MethodHead && method == http.MethodGet) {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	if method == http.MethodGet {
		return run(request.Context(), *identity, nil)
	}
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	return run(write.Context, *identity, write)
}

const campsNotAssociation = "You are not a sport association."

func campsPeriodJSON(result *service.CampsPeriodResult) map[string]any {
	row := result.Row
	var max any
	if row.Max != nil {
		max = *row.Max
	}
	services := result.Services
	if services == nil {
		services = []string{}
	}
	return map[string]any{"camps_and_retreats_period_id": row.ID, "start_date": dto.DRFUTCDateTime(row.Start),
		"end_date": dto.DRFUTCDateTime(row.End), "title": row.Title, "description": row.Description, "fee": result.Fee,
		"max_participants": max, "created_at": dto.DRFUTCDateTime(row.CreatedAt), "camps_and_retreats": row.CampID,
		"camps_and_Retreats_period_services": services}
}

func campsServiceJSON(row *domain.CampsService, fee string) map[string]any {
	return map[string]any{"camps_and_retreats_period_service_id": row.ID, "title": row.Title, "description": row.Description,
		"fee": fee, "created_at": dto.DRFUTCDateTime(row.CreatedAt), "camps_and_retreats_period": row.PeriodID,
		"payment_category": row.CategoryID}
}

// campsCategoryInfo is PaymentCategorySerializer(category).data; without a
// category it is the serializer's get_initial().
func campsCategoryInfo(category *domain.PaymentCategoryState) map[string]any {
	if category == nil {
		return map[string]any{"payment_category_id": nil, "name": "", "creation_date": nil, "sport_association": nil, "expense": false,
			"tax_deductible": false, "archived": false, "type": nil,
			"vat_management": map[string]any{"vat_management_id": nil, "vat": nil, "deduction": nil}, "deleted": false}
	}
	return dto.PaymentCategoryFromState(*category).JSONMap()
}

// campsPeriodInfoJSON is CampsAndRetreatsPeriodInfoSerializer's data: dates
// as %d/%m/%Y, comma fees and the services newest first.
func campsPeriodInfoJSON(info *service.CampsPeriodInfo) map[string]any {
	data := campsPeriodJSON(&info.Period)
	row := info.Period.Row
	data["start_date"] = row.Start.UTC().Format("02/01/2006")
	data["end_date"] = row.End.UTC().Format("02/01/2006")
	data["fee"] = dto.CampsCommaFee(row.Fee)
	services := make([]map[string]any, 0, len(info.Services))
	for index := range info.Services {
		item := &info.Services[index]
		value := campsServiceJSON(item, dto.CampsCommaFee(item.Fee))
		value["payment_category_info"] = campsCategoryInfo(item.Category)
		services = append(services, value)
	}
	data["services"] = services
	return data
}

func (h *CampsPeriodsHandler) AddPeriod(c echo.Context) error {
	return h.serve(c, http.MethodPost, "camps_and_retreats_periods_add", "Camps And Retreats Periods Add", "",
		func(ctx context.Context, identity domain.ResolvedIdentity, write *restWrite) error {
			result, err := h.camps.AddPeriod(ctx, identity, write.Body, write.Form, write.Audit)
			if err != nil {
				return campsError(c, err, true, campsNotAssociation)
			}
			return c.JSON(201, map[string]any{"message": "Added successfully", "period": campsPeriodJSON(result)})
		})
}

func (h *CampsPeriodsHandler) PeriodInfo(c echo.Context) error {
	uid := c.Param("uid")
	return h.serve(c, http.MethodGet, "camps_and_retreats_periods_info", "Camps And Retreats Periods Info",
		"This method is used to get a list of all camps and retreats",
		func(ctx context.Context, identity domain.ResolvedIdentity, _ *restWrite) error {
			info, err := h.camps.PeriodInfo(ctx, identity, uid)
			if err != nil {
				return campsError(c, err, false, "Only sport associations can view camps or retreats")
			}
			return c.JSON(200, map[string]any{"data": campsPeriodInfoJSON(info)})
		})
}

func (h *CampsPeriodsHandler) UpdatePeriod(c echo.Context) error {
	uid := c.Param("uid")
	return h.serve(c, http.MethodPatch, "camps_and_retreats_periods_update", "Camps And Retreats Periods Update", "",
		func(ctx context.Context, identity domain.ResolvedIdentity, write *restWrite) error {
			result, err := h.camps.UpdatePeriod(ctx, identity, uid, write.Body, write.Form, write.Audit)
			if err != nil {
				return campsError(c, err, false, campsNotAssociation)
			}
			return c.JSON(200, map[string]any{"message": "Updated successfully", "period": campsPeriodJSON(result)})
		})
}

func (h *CampsPeriodsHandler) DeletePeriod(c echo.Context) error {
	uid := c.Param("uid")
	return h.serve(c, http.MethodDelete, "camps_and_retreats_periods_delete", "Camps And Retreats Periods Delete", "",
		func(ctx context.Context, identity domain.ResolvedIdentity, write *restWrite) error {
			if err := h.camps.DeletePeriod(ctx, identity, uid, write.Audit); err != nil {
				return campsError(c, err, false, campsNotAssociation)
			}
			return c.JSON(200, map[string]string{"message": "Deleted successfully"})
		})
}

func (h *CampsPeriodsHandler) AddService(c echo.Context) error {
	return h.serve(c, http.MethodPost, "camps_and_retreats_periods_services_add", "Camps And Retreats Periods Services Add", "",
		func(ctx context.Context, identity domain.ResolvedIdentity, write *restWrite) error {
			row, fee, err := h.camps.AddService(ctx, identity, write.Body, write.Form, write.Audit)
			if err != nil {
				return campsError(c, err, true, campsNotAssociation)
			}
			return c.JSON(201, map[string]any{"message": "Added successfully", "service": campsServiceJSON(row, fee)})
		})
}

func (h *CampsPeriodsHandler) UpdateService(c echo.Context) error {
	uid := c.Param("uid")
	return h.serve(c, http.MethodPatch, "camps_and_retreats_periods_services_update", "Camps And Retreats Periods Services Update", "",
		func(ctx context.Context, identity domain.ResolvedIdentity, write *restWrite) error {
			row, err := h.camps.UpdateService(ctx, identity, uid, write.Body, write.Form, c.Request().Header.Get("X-Group-Id"), write.Audit)
			if err != nil {
				return campsError(c, err, false, campsNotAssociation)
			}
			return c.JSON(200, map[string]any{"message": "Updated successfully", "period": campsServiceJSON(row, row.Fee)})
		})
}

func (h *CampsPeriodsHandler) DeleteService(c echo.Context) error {
	uid := c.Param("uid")
	return h.serve(c, http.MethodDelete, "camps_and_retreats_periods_services_delete", "Camps And Retreats Periods Services Delete", "",
		func(ctx context.Context, identity domain.ResolvedIdentity, write *restWrite) error {
			if err := h.camps.DeleteService(ctx, identity, uid, write.Audit); err != nil {
				return campsError(c, err, false, campsNotAssociation)
			}
			return c.JSON(200, map[string]string{"message": "Deleted successfully"})
		})
}
