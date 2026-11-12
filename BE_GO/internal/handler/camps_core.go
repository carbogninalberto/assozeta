package handler

import (
	"context"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type CampsCoreService interface {
	List(ctx context.Context, identity domain.ResolvedIdentity) ([]domain.Camp, error)
	Info(ctx context.Context, identity domain.ResolvedIdentity, raw string) (*service.CampInfo, error)
	Add(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool, audit domain.LoginAuditContext) (*domain.Camp, error)
	Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, form bool, audit domain.LoginAuditContext) (*domain.Camp, error)
	Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error
}

// CampsCoreHandler serves the camps list, add, info, update and delete views
// through the camps periods dispatch.
type CampsCoreHandler struct {
	camps CampsCoreService
	base  *CampsPeriodsHandler
}

func NewCampsCoreHandler(camps CampsCoreService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *CampsCoreHandler {
	return &CampsCoreHandler{camps: camps, base: NewCampsPeriodsHandler(nil, actors, identities, audit, operations)}
}

const campsCoreDescription = "This method is used to get a list of all camps and retreats"

// campsCoreError is campsError with these views' not-found answer.
func campsCoreError(c echo.Context, err error, add bool, notAssociation string) error {
	if errors.Is(err, service.ErrCampsNotFound) {
		return c.JSON(404, map[string]string{"error": "Camp or retreat not found"})
	}
	return campsError(c, err, add, notAssociation)
}

// campJSON is CampsAndRetreatsSerializer's data.
func campJSON(row *domain.Camp) map[string]any {
	return map[string]any{"camps_and_retreats_id": row.ID, "title": row.Title, "description": row.Description,
		"sport_association": row.AssociationID}
}

func (h *CampsCoreHandler) List(c echo.Context) error {
	return h.base.serve(c, http.MethodGet, "camps_and_retreats_list", "Camps And Retreats List", campsCoreDescription,
		func(ctx context.Context, identity domain.ResolvedIdentity, _ *restWrite) error {
			rows, err := h.camps.List(ctx, identity)
			if err != nil {
				return campsCoreError(c, err, false, "Only sport associations can view camps or retreats")
			}
			data := make([]map[string]any, 0, len(rows))
			for index := range rows {
				data = append(data, campJSON(&rows[index]))
			}
			return c.JSON(200, map[string]any{"data": data})
		})
}

func (h *CampsCoreHandler) Info(c echo.Context) error {
	uid := c.Param("uid")
	return h.base.serve(c, http.MethodGet, "camps_and_retreats_info", "Camps And Retreats Info", campsCoreDescription,
		func(ctx context.Context, identity domain.ResolvedIdentity, _ *restWrite) error {
			info, err := h.camps.Info(ctx, identity, uid)
			if err != nil {
				return campsCoreError(c, err, false, "Only sport associations can view camps or retreats")
			}
			data := campJSON(&info.Camp)
			if info.Public != nil {
				data["sport_association"] = map[string]any{"sport_association_id": info.Camp.AssociationID,
					"denomination": info.Public.Denomination, "username": info.Public.Username}
			}
			periods := make([]map[string]any, 0, len(info.Periods))
			for index := range info.Periods {
				periods = append(periods, campsPeriodInfoJSON(&info.Periods[index]))
			}
			data["periods"] = periods
			return c.JSON(200, map[string]any{"data": data})
		})
}

func (h *CampsCoreHandler) Add(c echo.Context) error {
	return h.base.serve(c, http.MethodPost, "camps_and_retreats_add", "Camps And Retreats Add", "This method is used to add a new camp or retreat",
		func(ctx context.Context, identity domain.ResolvedIdentity, write *restWrite) error {
			row, err := h.camps.Add(ctx, identity, write.Body, write.Form, write.Audit)
			if err != nil {
				return campsCoreError(c, err, true, "Only sport associations can add camps or retreats")
			}
			return c.JSON(201, map[string]any{"message": "Camp or retreat added successfully", "camp_and_retreat": campJSON(row)})
		})
}

func (h *CampsCoreHandler) Update(c echo.Context) error {
	uid := c.Param("uid")
	return h.base.serve(c, http.MethodPatch, "camps_and_retreats_update", "Camps And Retreats Update", "",
		func(ctx context.Context, identity domain.ResolvedIdentity, write *restWrite) error {
			row, err := h.camps.Update(ctx, identity, uid, write.Body, write.Form, write.Audit)
			if err != nil {
				return campsCoreError(c, err, false, "Only sport associations can update camps or retreats")
			}
			return c.JSON(200, map[string]any{"message": "Camp or retreat updated successfully", "camp_and_retreat": campJSON(row)})
		})
}

func (h *CampsCoreHandler) Delete(c echo.Context) error {
	uid := c.Param("uid")
	return h.base.serve(c, http.MethodDelete, "camps_and_retreats_delete", "Camps And Retreats Delete", "",
		func(ctx context.Context, identity domain.ResolvedIdentity, write *restWrite) error {
			if err := h.camps.Delete(ctx, identity, uid, write.Audit); err != nil {
				return campsCoreError(c, err, false, "Only sport associations can delete camps or retreats")
			}
			return c.JSON(200, map[string]string{"message": "Camp or retreat deleted successfully"})
		})
}
