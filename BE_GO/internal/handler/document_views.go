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

// DocumentViewsHandler serves the HTML views of
// BE/docmanager/views/printing_views.py (@api_view(['GET'])): identity
// resolution, the view's permission classes, method dispatch, then the view.
type DocumentViewsHandler struct {
	views      *service.DocumentViews
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewDocumentViewsHandler(views *service.DocumentViews, actors *service.ActorAuthentication,
	identities *service.IdentityResolver) *DocumentViewsHandler {
	return &DocumentViewsHandler{views: views, actors: actors, identities: identities}
}

// Permission classes of the views.
const (
	viewAuthenticated = iota // IsAuthenticated
	viewOrBypass             // IsAuthenticated | HasBypassAuthorizationHeader
	viewAllowAny             // the AllowAny default (no view of this family)
)

// WithTutorPromotion lets the views save get_main_tutor's promoted relation:
// the request's audit context and the write admission.
func (h *DocumentViewsHandler) WithTutorPromotion(audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *DocumentViewsHandler {
	h.audit, h.operations = audit, operations
	return h
}

// documentViewPromotion admits the promotion when a view first needs it; the
// returned function releases the tracked operation after the response.
func documentViewPromotion(c echo.Context, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) (*service.DocumentViewPromotion, func()) {
	if audit == nil || operations == nil {
		return nil, func() {}
	}
	request := c.Request()
	var tracked *paymentcategorylifecycle.Operation
	var admitted context.Context
	var auditContext domain.LoginAuditContext
	promotion := &service.DocumentViewPromotion{Admit: func() (context.Context, domain.LoginAuditContext, error) {
		if admitted != nil {
			return admitted, auditContext, nil
		}
		operation, err := operations.Track(request.Context())
		if err != nil {
			return nil, auditContext, err
		}
		tracked = operation
		if auditContext, err = audit(request); err != nil {
			return nil, auditContext, err
		}
		admitted, err = tracked.Admit()
		return admitted, auditContext, err
	}}
	return promotion, func() {
		if tracked != nil {
			tracked.Release()
		}
	}
}

func (h *DocumentViewsHandler) Subscription(c echo.Context) error {
	return h.serve(c, service.ViewSubscription, "document_subscription_view", "Document Subscription View", viewAuthenticated)
}

func (h *DocumentViewsHandler) Preview(c echo.Context) error {
	return h.serve(c, service.ViewPreview, "document_subscription_preview", "Document Subscription Preview", viewAuthenticated)
}

func (h *DocumentViewsHandler) Compensation(c echo.Context) error {
	return h.serve(c, service.ViewCompensation, "document_compensation_view", "Document Compensation View", viewAuthenticated)
}

func (h *DocumentViewsHandler) Template(c echo.Context) error {
	return h.serve(c, service.ViewTemplate, "document_template_view", "Document Template View", viewOrBypass)
}

func (h *DocumentViewsHandler) MedicalAppointment(c echo.Context) error {
	return h.serve(c, service.ViewMedical, "document_medical_appointment_view", "Document Medical Appointment View", viewOrBypass)
}

func (h *DocumentViewsHandler) serve(c echo.Context, view, operation, name string, permission int) error {
	request := c.Request()
	identity, err := messageMutationsIdentityFor(c, h.actors, h.identities, http.MethodGet, operation, true)
	if identity == nil {
		return err
	}
	if identity.ScopeUser == nil && permission != viewAllowAny {
		allowed := false
		if permission == viewOrBypass {
			if allowed, err = h.views.AuthorizeBypass(requestAuthorization(request)); err != nil {
				return messageReadsError(c, err)
			}
		}
		if !allowed {
			c.Response().Header().Set("WWW-Authenticate", `Bearer realm="api"`)
			return restError(c, &restInputError{Status: 401, Detail: restText(request, "credentials")})
		}
	}
	switch request.Method {
	case http.MethodOptions:
		return supplierOptions(c, name, "")
	case http.MethodGet:
	default:
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	promotion, release := documentViewPromotion(c, h.audit, h.operations)
	defer release()
	reply, err := h.views.Render(request.Context(), *identity, service.DocumentViewRequest{
		View: view, UID: c.Param("uid"), Path: request.URL.Path, Query: parseReferenceForm(request.URL.RawQuery),
		Group: request.Header.Get("X-Group-Id"), Language: restLanguage(request), Authorization: requestAuthorization(request),
		Promotion: promotion,
	})
	if err != nil {
		if errors.Is(err, service.ErrDocumentViewNotPorted) {
			c.Logger().Warn(err.Error())
		}
		if errors.Is(err, paymentcategorylifecycle.ErrAdmissionStopped) {
			return paymentCategoryError(c, err)
		}
		return messageReadsError(c, err)
	}
	if reply.HTML != nil {
		// translation.activate('it') in a view changes LocaleMiddleware's Content-Language.
		c.Response().Header().Set("Content-Language", reply.Language)
		return c.Blob(reply.Status, "text/html; charset=utf-8", []byte(*reply.HTML))
	}
	return c.JSON(reply.Status, reply.JSON)
}
