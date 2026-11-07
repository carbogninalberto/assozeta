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

type CampsSubscriptionsService interface {
	List(ctx context.Context, identity domain.ResolvedIdentity, raw, group string) ([]service.CampSubscriptionListItem, error)
	Add(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, form bool, group string,
		audit domain.LoginAuditContext) (*service.CampSubscriptionResult, error)
	Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, form bool, group string, audit domain.LoginAuditContext) error
	Delete(ctx context.Context, identity domain.ResolvedIdentity, raw, group string, audit domain.LoginAuditContext) error
}

// CampsSubscriptionsHandler serves the camp subscription list, add, update and
// delete views through the camps periods dispatch.
type CampsSubscriptionsHandler struct {
	subscriptions CampsSubscriptionsService
	base          *CampsPeriodsHandler
}

func NewCampsSubscriptionsHandler(subscriptions CampsSubscriptionsService, actors *service.ActorAuthentication,
	identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *CampsSubscriptionsHandler {
	return &CampsSubscriptionsHandler{subscriptions: subscriptions, base: NewCampsPeriodsHandler(nil, actors, identities, audit, operations)}
}

func campsSubscriptionsMessage(code string, english bool) string {
	if input, ok := strings.CutPrefix(code, dto.CampsValidationChoice); ok {
		if english {
			return `"` + input + `" is not a valid choice.`
		}
		return `"` + input + `" non è una scelta valida.`
	}
	return campsValidationMessage(code, english)
}

func campsSubscriptionsCode(code string) string {
	switch {
	case strings.HasPrefix(code, dto.CampsValidationChoice):
		return "invalid_choice"
	case code == dto.PaymentCategoryValidationUUID:
		return "invalid"
	}
	return dto.CampsDRFCode(code)
}

func campsErrorDetail(request *http.Request, code string) string {
	english := restLanguage(request) == "en"
	return "ErrorDetail(string=" + dto.PythonStringRepr(campsSubscriptionsMessage(code, english)) + ", code=" +
		dto.PythonStringRepr(campsSubscriptionsCode(code)) + ")"
}

// campsSubscriptionsError maps these views' answers onto the camps ones.
func campsSubscriptionsError(c echo.Context, err error, notAssociation string) error {
	request := c.Request()
	var invalid *service.CampsValidationError
	var single *service.CampsSingleInvalid
	var field *service.CampsFieldInvalid
	var uuid *service.CampsAddUUIDError
	switch {
	case errors.As(err, &invalid):
		var out strings.Builder
		out.WriteString("{")
		for index, item := range invalid.Fields {
			if index > 0 {
				out.WriteString(", ")
			}
			out.WriteString(dto.PythonStringRepr(item.Name) + ": [")
			for position, code := range item.Codes {
				if position > 0 {
					out.WriteString(", ")
				}
				out.WriteString(campsErrorDetail(request, code))
			}
			out.WriteString("]")
		}
		out.WriteString("}")
		return c.JSON(400, map[string]string{"error": out.String()})
	case errors.As(err, &single):
		return c.JSON(400, map[string]string{"error": "{" + dto.PythonStringRepr(single.Field) + ": ErrorDetail(string=" +
			dto.PythonStringRepr(single.Message) + ", code='invalid')}"})
	case errors.As(err, &field):
		return c.JSON(400, map[string]string{"error": "[" + campsErrorDetail(request, field.Code) + "]"})
	case errors.As(err, &uuid):
		return c.JSON(400, map[string]string{"error": "[ErrorDetail(string=" + dto.PythonStringRepr(uuid.Detail) + ", code='invalid')]"})
	}
	// The views re-raise PermissionDenied, the tenant scope's included.
	return campsError(c, err, false, notAssociation)
}

func campSubscriptionPeriodJSON(row domain.CampSubscriptionPeriod) map[string]any {
	return map[string]any{"camps_and_retreats_subscription_period_id": row.ID, "camps_and_retreats_period": row.PeriodID,
		"camps_and_retreats_period_services": row.Services, "camps_and_retreats_subscription": row.SubscriptionID, "payment": row.PaymentID}
}

func campSubscriptionJSON(row domain.CampSubscription) map[string]any {
	return map[string]any{"camps_and_retreats_subscription_id": row.ID, "status_flag": row.Status, "created_at": dto.DRFUTCDateTime(row.CreatedAt),
		"camps_and_retreats": row.CampID, "subscription": row.RegistrationID}
}

// campSubscriptionListJSON is CampsAndRetreatsSubscriptionListInfoSerializer's data.
func campSubscriptionListJSON(item *service.CampSubscriptionListItem) map[string]any {
	data := campSubscriptionJSON(item.Row)
	registration := item.Registration
	var associate any
	if registration.AssociateID != nil {
		associate = map[string]any{"associate_id": *registration.AssociateID, "first_name": registration.FirstName,
			"last_name": registration.LastName, "tax_code": registration.TaxCode, "family": registration.FamilyID,
			"family_role": registration.FamilyRole}
	}
	data["subscription"] = map[string]any{"subscription_id": registration.ID, "associate": associate, "current_year": item.Current}
	periods := make([]map[string]any, 0, len(item.Periods))
	selected := map[string]any{}
	services := map[string]any{}
	for index := range item.Periods {
		period := &item.Periods[index]
		var payment any
		if period.Payment != nil {
			payment = dto.CampsListPayment(*period.Payment)
		}
		all := make([]map[string]any, 0, len(period.Services))
		for position := range period.Services {
			service := &period.Services[position]
			value := campsServiceJSON(service, dto.CampsCommaFee(service.Fee))
			value["payment_category_info"] = campsCategoryInfo(service.Category)
			all = append(all, value)
		}
		periods = append(periods, map[string]any{"camps_and_retreats_subscription_period_id": period.Row.ID, "payment": payment,
			"camps_and_retreats_period": campsPeriodInfoJSON(&period.Period), "camps_and_retreats_period_services": all,
			"camps_and_retreats_subscription": period.Row.SubscriptionID})
		selected[period.Row.PeriodID] = true
		chosen := map[string]any{}
		for _, service := range period.Row.Services {
			chosen[service] = true
		}
		services[period.Row.PeriodID] = chosen
	}
	paid := map[string]any{}
	for _, period := range item.PaidPeriods {
		paid[period] = true
	}
	data["periods"], data["selected_periods"], data["selected_services"], data["paid_periods"] = periods, selected, services, paid
	return data
}

func (h *CampsSubscriptionsHandler) List(c echo.Context) error {
	uid := c.Param("uid")
	return h.base.serve(c, http.MethodGet, "camps_and_retreats_subscriptions_list", "Camps And Retreats Subscriptions List", "",
		func(ctx context.Context, identity domain.ResolvedIdentity, _ *restWrite) error {
			items, err := h.subscriptions.List(ctx, identity, uid, c.Request().Header.Get("X-Group-Id"))
			if err != nil {
				return campsSubscriptionsError(c, err, "You are not a sport association")
			}
			data := make([]map[string]any, 0, len(items))
			for index := range items {
				data = append(data, campSubscriptionListJSON(&items[index]))
			}
			return c.JSON(200, map[string]any{"data": data})
		})
}

func (h *CampsSubscriptionsHandler) Add(c echo.Context) error {
	uid := c.Param("uid")
	return h.base.serve(c, http.MethodPost, "camps_and_retreats_subscriptions_add", "Camps And Retreats Subscriptions Add", "",
		func(ctx context.Context, identity domain.ResolvedIdentity, write *restWrite) error {
			result, err := h.subscriptions.Add(ctx, identity, uid, write.Body, write.Form, c.Request().Header.Get("X-Group-Id"), write.Audit)
			if err != nil {
				return campsSubscriptionsError(c, err, campsNotAssociation)
			}
			data := campSubscriptionJSON(result.Row)
			periods := make([]map[string]any, 0, len(result.Periods))
			for _, period := range result.Periods {
				periods = append(periods, campSubscriptionPeriodJSON(period))
			}
			data["periods"] = periods
			return c.JSON(201, map[string]any{"message": "Added successfully", "subscription": data})
		})
}

func (h *CampsSubscriptionsHandler) Update(c echo.Context) error {
	uid := c.Param("uid")
	return h.base.serve(c, http.MethodPatch, "camps_and_retreats_subscriptions_update", "Camps And Retreats Subscriptions Update", "",
		func(ctx context.Context, identity domain.ResolvedIdentity, write *restWrite) error {
			if err := h.subscriptions.Update(ctx, identity, uid, write.Body, write.Form, c.Request().Header.Get("X-Group-Id"), write.Audit); err != nil {
				return campsSubscriptionsError(c, err, campsNotAssociation)
			}
			return c.JSON(200, map[string]string{"message": "Updated successfully"})
		})
}

func (h *CampsSubscriptionsHandler) Delete(c echo.Context) error {
	uid := c.Param("uid")
	return h.base.serve(c, http.MethodDelete, "camps_and_retreats_subscriptions_delete", "Camps And Retreats Subscriptions Delete", "",
		func(ctx context.Context, identity domain.ResolvedIdentity, write *restWrite) error {
			if err := h.subscriptions.Delete(ctx, identity, uid, c.Request().Header.Get("X-Group-Id"), write.Audit); err != nil {
				return campsSubscriptionsError(c, err, campsNotAssociation)
			}
			return c.JSON(200, map[string]string{"message": "Deleted successfully"})
		})
}
