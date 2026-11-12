package handler

import (
	"context"
	"errors"
	"fmt"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type AssociateDraftService interface {
	List(ctx context.Context, identity domain.ResolvedIdentity, audit domain.LoginAuditContext) (any, error)
	AddAssociation(ctx context.Context, identity domain.ResolvedIdentity) error
	Add(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) error
	Edit(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, audit domain.LoginAuditContext) (any, error)
	Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error
	BulkDelete(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) (int, int, error)
}

type AssociateDraftsHandler struct {
	drafts     AssociateDraftService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewAssociateDraftsHandler(drafts AssociateDraftService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *AssociateDraftsHandler {
	return &AssociateDraftsHandler{drafts: drafts, actors: actors, identities: identities, audit: audit, operations: operations}
}

// failure maps the views' responses; anything else is the 500 page.
func (h *AssociateDraftsHandler) failure(c echo.Context, err error) error {
	var invalid *service.PaymentInfoUUIDError
	var denied *domain.PermissionDenied
	switch {
	case errors.Is(err, domain.ErrAssociateDraftUnauthorized):
		return c.NoContent(http.StatusUnauthorized) // Response(status=401): no challenge
	case errors.As(err, &invalid):
		return c.JSON(400, []string{invalid.Detail}) // is_valid_uuid
	case errors.As(err, &denied):
		return restError(c, &domain.PermissionDenied{Detail: messageReadsPermissionDetail(c.Request())})
	case errors.Is(err, domain.ErrAssociateDraftNotFound):
		return c.JSON(404, map[string]string{"msg": "not found."})
	case errors.Is(err, domain.ErrAssociateDraftNotList):
		return c.JSON(400, map[string]string{"msg": "La richiesta deve contenere una lista di ID da eliminare."})
	}
	return messageReadsError(c, err)
}

// python renders a parsed body with Python's float repr and key order.
func associateDraftJSON(c echo.Context, value any) error {
	return c.Blob(http.StatusOK, echo.MIMEApplicationJSON, dto.PythonJSONDumpsCompact(value))
}

// serve runs one @api_view function: identity and the registry, OPTIONS, the
// method check, then run (which admits the write when it needs the body).
func (h *AssociateDraftsHandler) serve(c echo.Context, method, operation, name string, run func(identity domain.ResolvedIdentity) error) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, method, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, name, "")
	}
	if request.Method != method {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	return run(*identity)
}

// admitted reads request.data and admits the write.
func (h *AssociateDraftsHandler) admitted(c echo.Context, run func(write *restWrite) error) error {
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	return run(write)
}

func (h *AssociateDraftsHandler) List(c echo.Context) error {
	return h.serve(c, http.MethodGet, "subscription_associates_draft_list", "Subscription Associates Draft List",
		func(identity domain.ResolvedIdentity) error {
			request := c.Request()
			tracked, err := h.operations.Track(request.Context())
			if err != nil {
				return paymentCategoryError(c, err)
			}
			defer tracked.Release()
			auditContext, err := h.audit(request)
			if err != nil {
				return restError(c, err)
			}
			ctx, err := tracked.Admit()
			if err != nil {
				return paymentCategoryError(c, err)
			}
			result, err := h.drafts.List(ctx, identity, auditContext)
			if err != nil {
				return h.failure(c, err)
			}
			return associateDraftJSON(c, result)
		})
}

func (h *AssociateDraftsHandler) Add(c echo.Context) error {
	return h.serve(c, http.MethodPost, "subscription_associates_draft_add", "Subscription Associates Draft Add",
		func(identity domain.ResolvedIdentity) error {
			// The association is read before request.data.
			if err := h.drafts.AddAssociation(c.Request().Context(), identity); err != nil {
				return h.failure(c, err)
			}
			return h.admitted(c, func(write *restWrite) error {
				if err := h.drafts.Add(write.Context, identity, write.Body, write.Audit); err != nil {
					return h.failure(c, err)
				}
				return c.JSON(http.StatusOK, map[string]string{"msg": "Draft saved!"})
			})
		})
}

func (h *AssociateDraftsHandler) Edit(c echo.Context) error {
	return h.serve(c, http.MethodPost, "subscription_associates_draft_edit", "Subscription Associates Draft Edit",
		func(identity domain.ResolvedIdentity) error {
			return h.admitted(c, func(write *restWrite) error {
				result, err := h.drafts.Edit(write.Context, identity, c.Param("uid"), write.Body, write.Audit)
				if err != nil {
					return h.failure(c, err)
				}
				return associateDraftJSON(c, result)
			})
		})
}

func (h *AssociateDraftsHandler) Delete(c echo.Context) error {
	return h.serve(c, http.MethodPost, "subscription_associates_draft_delete", "Subscription Associates Draft Delete",
		func(identity domain.ResolvedIdentity) error {
			return h.admitted(c, func(write *restWrite) error {
				if err := h.drafts.Delete(write.Context, identity, c.Param("uid"), write.Audit); err != nil {
					return h.failure(c, err)
				}
				return c.JSON(http.StatusOK, map[string]string{"msg": "successfully deleted."})
			})
		})
}

func (h *AssociateDraftsHandler) BulkDelete(c echo.Context) error {
	return h.serve(c, http.MethodDelete, "subscription_associates_draft_bulk_delete", "Subscription Associates Draft Bulk Delete",
		func(identity domain.ResolvedIdentity) error {
			// The athlete check precedes request.data.
			if identity.ScopeUser.Role == domain.RoleAthlete {
				return h.failure(c, domain.ErrAssociateDraftUnauthorized)
			}
			return h.admitted(c, func(write *restWrite) error {
				deleted, total, err := h.drafts.BulkDelete(write.Context, identity, write.Body, write.Audit)
				if err != nil {
					return h.failure(c, err)
				}
				return c.JSON(http.StatusOK, map[string]any{"msg": fmt.Sprintf("%d/%d bozze eliminate con successo.", deleted, total),
					"deleted_count": deleted, "total_count": total})
			})
		})
}
