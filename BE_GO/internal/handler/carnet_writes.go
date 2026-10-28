package handler

import (
	"context"
	"errors"
	"mime"
	"net/http"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type CarnetDeleteService interface {
	Delete(context.Context, domain.ResolvedIdentity, string, domain.LoginAuditContext) error
}

type CarnetUpdateService interface {
	Update(context.Context, domain.ResolvedIdentity, string, any, bool, domain.LoginAuditContext) error
}

type CarnetUnassignService interface {
	Unassign(context.Context, domain.ResolvedIdentity, string, domain.LoginAuditContext) error
}

type CarnetToggleService interface {
	Toggle(context.Context, domain.ResolvedIdentity, string, bool, domain.LoginAuditContext) error
}

type CarnetLinkService interface {
	Remove(context.Context, domain.ResolvedIdentity, string, string) error
}

type CarnetMetaService interface {
	UpdateLessons(context.Context, domain.ResolvedIdentity, string, any, domain.LoginAuditContext) error
}

type CarnetWritesHandler struct {
	replaces   CarnetReplaceService
	assigns    CarnetAssignService
	topups     CarnetTopupService
	metas      CarnetMetaService
	links      CarnetLinkService
	toggles    CarnetToggleService
	deletes    CarnetDeleteService
	updates    CarnetUpdateService
	unassigns  CarnetUnassignService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

type CarnetReplaceService interface {
	Replace(ctx context.Context, identity domain.ResolvedIdentity, carnet, subscription, group string, body any, audit domain.LoginAuditContext) error
}

type CarnetAssignService interface {
	Assign(ctx context.Context, identity domain.ResolvedIdentity, carnet, subscription, group string, body any, audit domain.LoginAuditContext) (*string, error)
}

type CarnetTopupService interface {
	Topup(context.Context, domain.ResolvedIdentity, string, string, domain.LoginAuditContext) error
}

func NewCarnetWritesHandler(replaces CarnetReplaceService, assigns CarnetAssignService, topups CarnetTopupService, metas CarnetMetaService, links CarnetLinkService, toggles CarnetToggleService, deletes CarnetDeleteService, updates CarnetUpdateService, unassigns CarnetUnassignService, actors *service.ActorAuthentication, identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *CarnetWritesHandler {
	return &CarnetWritesHandler{replaces: replaces, assigns: assigns, topups: topups, metas: metas, links: links, toggles: toggles, deletes: deletes, updates: updates, unassigns: unassigns, actors: actors, identities: identities, audit: audit, operations: operations}
}

func (h *CarnetWritesHandler) Delete(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "carnet_delete")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Carnet Delete", "Delete a carnet")
	}
	if request.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	audit, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	err = h.deletes.Delete(ctx, *identity, c.Param("uid"), audit)
	switch {
	case errors.Is(err, service.ErrCarnetNotAllowed):
		return c.JSON(403, map[string]string{"msg": "Not allowed."})
	case errors.Is(err, domain.ErrCarnetNotFound):
		return c.JSON(404, map[string]string{"msg": "Carnet not found."})
	case err != nil:
		return restError(c, err)
	}
	return c.JSON(200, map[string]string{"msg": "Carnet deleted successfully."})
}

func (h *CarnetWritesHandler) Update(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPatch, "carnet_update")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Carnet Update", "Update a carnet")
	}
	if request.Method != http.MethodPatch {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	body, err := readMessageMutationsBody(c)
	if err != nil {
		return restError(c, err)
	}
	media, _, _ := mime.ParseMediaType(request.Header.Get("Content-Type"))
	audit, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	err = h.updates.Update(ctx, *identity, c.Param("uid"), body, media == "application/x-www-form-urlencoded" || media == "multipart/form-data", audit)
	var invalid *dto.CarnetUpdateValidationError
	switch {
	case errors.As(err, &invalid):
		fields := make(map[string]any, len(invalid.Fields))
		for name, codes := range invalid.Fields {
			fields[name] = codes
		}
		return c.JSON(400, map[string]any{"msg": paymentCategoryValidationMessages(request, fields)})
	case errors.Is(err, service.ErrCarnetNotAllowed):
		return c.JSON(403, map[string]string{"msg": "Not allowed."})
	case errors.Is(err, domain.ErrCarnetNotFound):
		return c.JSON(404, map[string]string{"msg": "Carnet not found."})
	case err != nil:
		return restError(c, err)
	}
	return c.JSON(200, map[string]string{"msg": "Carnet updated successfully."})
}

// Unassign has no permission class in Django: an anonymous request reaches
// request.user.role and fails (500).
func (h *CarnetWritesHandler) Unassign(c echo.Context) error {
	if c.Request().Header.Get("Authorization") == "" && c.Request().Method == http.MethodDelete {
		c.Response().Header().Set("Allow", "DELETE, OPTIONS")
		return restError(c, errCarnetAnonymous)
	}
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodDelete, "carnet_unassign")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Carnet Unassign", "Remove a carnet from a subscription and delete the\nunpaid payments")
	}
	if request.Method != http.MethodDelete {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	audit, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	err = h.unassigns.Unassign(ctx, *identity, c.Param("uid_subscription"), audit)
	switch {
	case errors.Is(err, service.ErrCarnetNotAllowed):
		return c.JSON(403, map[string]string{"msg": "Not allowed."})
	case errors.Is(err, domain.ErrCarnetNotFound):
		return c.JSON(404, map[string]string{"msg": "Carnet not found."})
	case err != nil:
		return restError(c, err)
	}
	return c.JSON(200, map[string]string{"msg": "Carnet removed successfully."})
}

var errCarnetAnonymous = errors.New("anonymous user has no role")

func (h *CarnetWritesHandler) Disable(c echo.Context) error { return h.toggle(c, true) }
func (h *CarnetWritesHandler) Enable(c echo.Context) error  { return h.toggle(c, false) }

func (h *CarnetWritesHandler) toggle(c echo.Context, disabled bool) error {
	action, word := "enable", "enabled"
	if disabled {
		action, word = "disable", "disabled"
	}
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPatch, "carnet_subscription_"+action)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Carnet Subscription "+strings.ToUpper(action[:1])+action[1:], "Set carnet subscription to "+word)
	}
	if request.Method != http.MethodPatch {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	audit, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	err = h.toggles.Toggle(ctx, *identity, c.Param("uid"), disabled, audit)
	switch {
	case errors.Is(err, service.ErrCarnetNotAllowed):
		return c.JSON(403, map[string]string{"msg": "Not allowed."})
	case errors.Is(err, domain.ErrCarnetSubscriptionNotFound):
		return c.JSON(404, map[string]string{"msg": "Carnet subscription not found."})
	case err != nil:
		return restError(c, err)
	}
	return c.JSON(200, map[string]string{"msg": "Carnet subscription " + word + "."})
}

func (h *CarnetWritesHandler) RemoveCourseLink(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodDelete, "carnet_subscription_delete")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Carnet Subscription Delete", "Set carnet subscription delete")
	}
	if request.Method != http.MethodDelete {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	err = h.links.Remove(ctx, *identity, c.Param("uid"), c.Param("uid_course"))
	switch {
	case errors.Is(err, service.ErrCarnetNotAllowed):
		return c.JSON(403, map[string]string{"msg": "Not allowed."})
	case errors.Is(err, domain.ErrCarnetSubscriptionNotFound):
		return c.JSON(404, map[string]string{"msg": "Carnet subscription not found."})
	case err != nil:
		return restError(c, err)
	}
	return c.JSON(200, map[string]string{"msg": "Carnet subscription deleted."})
}

func (h *CarnetWritesHandler) UpdateLessons(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPatch, "carnet_subscription_update")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Carnet Subscription Update", "Set carnet subscription to enabled")
	}
	if request.Method != http.MethodPatch {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	body, err := readMessageMutationsBody(c)
	if err != nil {
		return restError(c, err)
	}
	audit, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	err = h.metas.UpdateLessons(ctx, *identity, c.Param("uid"), body, audit)
	switch {
	case errors.Is(err, service.ErrCarnetNotAllowed):
		return c.JSON(403, map[string]string{"msg": "Not allowed."})
	case errors.Is(err, service.ErrCarnetLessonsMissing):
		return c.JSON(400, map[string]string{"msg": "Missing lessons left."})
	case errors.Is(err, service.ErrCarnetLessonsRange):
		return c.JSON(400, map[string]string{"msg": "Lessons left cannot be > than lessons_number."})
	case errors.Is(err, domain.ErrCarnetSubscriptionNotFound):
		return c.JSON(404, map[string]string{"msg": "Carnet subscription not found."})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, map[string]string{"msg": "Carnet subscription updated."})
}

func (h *CarnetWritesHandler) Topup(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "carnet_subscription_topup")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Carnet Subscription Topup", "Create a new carnet with the same subscription")
	}
	if request.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	audit, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	err = h.topups.Topup(ctx, *identity, c.Param("uid"), request.Header.Get("X-Group-Id"), audit)
	switch {
	case errors.Is(err, service.ErrCarnetNotAllowed):
		return c.JSON(403, map[string]string{"msg": "Not allowed."})
	case errors.Is(err, domain.ErrCarnetSubscriptionNotFound):
		return c.JSON(404, map[string]string{"msg": "Error in topping up carnet."})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, map[string]string{"msg": "Carnet topped up."})
}

func (h *CarnetWritesHandler) Assign(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "carnet_assign")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Carnet Assign", "Add a new carnet to a subscription")
	}
	if request.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	body, err := readMessageMutationsBody(c)
	if err != nil {
		return restError(c, err)
	}
	audit, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	course, err := h.assigns.Assign(ctx, *identity, c.Param("uid"), c.Param("uid_subscription"), request.Header.Get("X-Group-Id"), body, audit)
	switch {
	case errors.Is(err, service.ErrCarnetNotAllowed):
		return c.JSON(403, map[string]string{"msg": "Not allowed."})
	case errors.Is(err, domain.ErrCarnetAssignSubscription):
		return c.JSON(404, map[string]string{"msg": "Subscription not found."})
	case errors.Is(err, domain.ErrCarnetAssignAssociation):
		return c.JSON(404, map[string]string{"msg": "Sport association not found."})
	case errors.Is(err, domain.ErrCarnetAssignCarnet):
		return c.JSON(404, map[string]string{"msg": "Carnet not found."})
	case errors.Is(err, domain.ErrCarnetSubscriptionNotFound):
		return c.JSON(404, map[string]string{"msg": "Carnet subscription not found."})
	case errors.Is(err, domain.ErrCarnetAssignCourse):
		return c.JSON(404, map[string]string{"msg": "Course subscription not found."})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, map[string]any{"msg": "Carnet assigned successfully.", "course_subscription_id": course})
}

func (h *CarnetWritesHandler) Replace(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "carnet_replace")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Carnet Replace", "Add a new carnet to the association")
	}
	if request.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	body, err := readMessageMutationsBody(c)
	if err != nil {
		return restError(c, err)
	}
	audit, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	err = h.replaces.Replace(ctx, *identity, c.Param("uid"), c.Param("uid_subscription"), request.Header.Get("X-Group-Id"), body, audit)
	switch {
	case errors.Is(err, service.ErrCarnetNotAllowed):
		return c.JSON(403, map[string]string{"msg": "Not allowed."})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, map[string]string{"msg": "Carnet added successfully."})
}
