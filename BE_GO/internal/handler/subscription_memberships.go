package handler

import (
	"context"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type SubscriptionMembershipService interface {
	List(ctx context.Context, identity domain.ResolvedIdentity, request service.SubscriptionMembershipRequest) (map[string]any, error)
	AddAssociation(ctx context.Context, identity domain.ResolvedIdentity) error
	Add(ctx context.Context, identity domain.ResolvedIdentity, request service.SubscriptionMembershipRequest, body any, form bool,
		audit domain.LoginAuditContext) (map[string]any, error)
	Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, request service.SubscriptionMembershipRequest, body any, form bool,
		audit domain.LoginAuditContext) (map[string]any, error)
	Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, request service.SubscriptionMembershipRequest,
		audit domain.LoginAuditContext) error
	DeleteWithoutPK(ctx context.Context, identity domain.ResolvedIdentity, request service.SubscriptionMembershipRequest) error
}

type SubscriptionMembershipsHandler struct {
	memberships SubscriptionMembershipService
	actors      *service.ActorAuthentication
	identities  *service.IdentityResolver
	audit       LoginAuditReader
	operations  *paymentcategorylifecycle.Lifecycle
}

func NewSubscriptionMembershipsHandler(memberships SubscriptionMembershipService, actors *service.ActorAuthentication,
	identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *SubscriptionMembershipsHandler {
	return &SubscriptionMembershipsHandler{memberships: memberships, actors: actors, identities: identities, audit: audit, operations: operations}
}

// subscriptionMembershipAddActions is SimpleMetadata's POST description of
// SubscriptionMembershipSerializer, the only PUT/POST method allowed on add.
var subscriptionMembershipAddActions = func() map[string]any {
	field := func(kind, label string, extra map[string]any) map[string]any {
		value := map[string]any{"type": kind, "required": false, "read_only": false, "label": label}
		for key, item := range extra {
			value[key] = item
		}
		return value
	}
	readOnly := map[string]any{"read_only": true}
	return map[string]any{"POST": map[string]any{
		"subscription_membership_id": field("string", "Subscription membership id", nil),
		"sport_association":          field("field", "Sport association", map[string]any{"required": true}),
		"associate_id":               field("string", "Associate id", readOnly),
		"associate":                  field("field", "Associate", nil),
		"creation_date":              field("datetime", "Creation date", nil),
		"start_date":                 field("date", "Start date", nil),
		"end_date":                   field("date", "End date", nil),
		"membership_type":            field("string", "Membership type", map[string]any{"max_length": 255}),
		"membership_number":          field("string", "Membership number", map[string]any{"max_length": 50}),
		"description":                field("string", "Description", map[string]any{"max_length": 255}),
		"price":                      field("decimal", "Price", map[string]any{"max_digits": 9, "decimal_places": 2}),
		"paid":                       field("field", "Paid", readOnly),
		"payment":                    field("field", "Payment", nil),
		"attached_membership_documents": field("field", "Attached membership documents", map[string]any{"read_only": true,
			"child": map[string]any{"type": "nested object", "required": false, "read_only": true, "children": map[string]any{
				"document_id": field("string", "Document id", nil), "creation_date": field("datetime", "Creation date", nil),
				"filepath":        field("string", "Filepath", map[string]any{"max_length": 1000}),
				"filename":        field("string", "Filename", map[string]any{"required": true, "max_length": 255}),
				"file_size_bytes": field("integer", "File size bytes", map[string]any{"min_value": 0, "max_value": int64(9223372036854775807)}),
				"token":           field("string", "Token", nil)}}}),
		"documents_data": field("list", "Documents data", map[string]any{"child": map[string]any{"type": "nested object", "required": true,
			"read_only": false, "child": map[string]any{"type": "field", "required": true, "read_only": false}}}),
	}}
}()

// subscriptionMembershipMessages renders DRF's field errors, including the
// DateField format message for DATE_INPUT_FORMATS ['%d/%m/%Y'].
func subscriptionMembershipMessages(request *http.Request, fields map[string]any) any {
	english := restLanguage(request) == "en"
	translated := make(map[string]any, len(fields))
	for name, value := range fields {
		if codes, ok := value.([]string); ok {
			messages := make([]string, len(codes))
			for index, code := range codes {
				messages[index] = code
				if code == dto.SubscriptionMembershipDate {
					messages[index] = "La data è in un formato errato. Usa uno dei seguenti formati: DD/MM/YYYY."
					if english {
						messages[index] = "Date has wrong format. Use one of these formats instead: DD/MM/YYYY."
					}
				}
			}
			value = messages
		}
		translated[name] = value
	}
	return paymentCategoryValidationMessages(request, translated)
}

func (h *SubscriptionMembershipsHandler) request(c echo.Context) service.SubscriptionMembershipRequest {
	request := c.Request()
	return service.SubscriptionMembershipRequest{Query: parseReferenceForm(request.URL.RawQuery), GroupHeader: request.Header.Get("X-Group-Id")}
}

func (h *SubscriptionMembershipsHandler) failure(c echo.Context, err error) error {
	request := c.Request()
	var invalid *service.SubscriptionMembershipValidationError
	switch {
	case errors.As(err, &invalid):
		return c.JSON(400, subscriptionMembershipMessages(request, invalid.Fields))
	case errors.Is(err, domain.ErrSubscriptionMembershipForeignReference):
		return restError(c, &domain.PermissionDenied{Detail: messageReadsPermissionDetail(request)})
	case errors.Is(err, domain.ErrSubscriptionMembershipNotFound):
		return c.JSON(404, map[string]string{"detail": "No SubscriptionMembership matches the given query."})
	case errors.Is(err, domain.ErrSubscriptionMembershipMalformed):
		return restError(c, &restInputError{Status: 404, Detail: restText(request, "notfound")})
	case errors.Is(err, service.ErrSubscriptionMembershipDocumentKey):
		return c.JSON(400, map[string]string{"exception": "document key not present"})
	}
	return messageReadsError(c, err)
}

// List serves subscription/memberships/list: as_view({'get': 'list'}) (GET
// also answers HEAD) plus the viewset's own delete().
func (h *SubscriptionMembershipsHandler) List(c echo.Context) error {
	c.Response().Header().Set("Allow", "GET, HEAD, DELETE, OPTIONS")
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, "subscription_membership_list")
	if identity == nil {
		return err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return supplierOptions(c, "Subscription Membership", "")
	case http.MethodGet, http.MethodHead:
		result, err := h.memberships.List(request.Context(), *identity, h.request(c))
		if err != nil {
			return h.failure(c, err)
		}
		return c.JSON(http.StatusOK, result)
	case http.MethodDelete:
		return h.failure(c, h.memberships.DeleteWithoutPK(request.Context(), *identity, h.request(c)))
	}
	return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
}

// write dispatches like as_view({method: action}) for the write routes; the
// viewset's own delete() also answers DELETE on them.
func (h *SubscriptionMembershipsHandler) write(c echo.Context, method, operation string, actions map[string]any,
	run func(ctx context.Context, identity domain.ResolvedIdentity, write *restWrite) (int, any, error),
	remove func(ctx context.Context, identity domain.ResolvedIdentity, audit domain.LoginAuditContext) error) error {
	allow := method + ", DELETE, OPTIONS"
	if method == http.MethodDelete {
		allow = "DELETE, OPTIONS"
	}
	c.Response().Header().Set("Allow", allow)
	if r := c.Request(); r.Method == method && method != http.MethodDelete {
		// A multipart body keeps its files: parse it once, before identity resolution and the view read request.data.
		raw, err := bufferMultipart(c)
		if err != nil {
			return restError(c, err)
		}
		if raw != nil {
			_, _ = readAuthenticatedRESTBodyUsing(c, func(*http.Request) (any, error) { return uploadBody(c, raw)() })
		}
	}
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, method, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		if actions == nil {
			return supplierOptions(c, "Subscription Membership", "")
		}
		return c.JSON(200, map[string]any{"name": "Subscription Membership", "description": "", "renders": []string{"application/json"},
			"parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}, "actions": actions})
	}
	if request.Method != method && request.Method != http.MethodDelete {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	if request.Method == http.MethodPost {
		// add evaluates request.user.sport_association before request.data.
		if err := h.memberships.AddAssociation(request.Context(), *identity); err != nil {
			return h.failure(c, err)
		}
	}
	if request.Method == http.MethodDelete {
		// delete() never reads request.data.
		return h.remove(c, *identity, remove)
	}
	admitted, release, err := admitRESTWrite(c, h.operations, h.audit)
	if admitted == nil {
		return err
	}
	defer release()
	status, result, err := run(admitted.Context, *identity, admitted)
	if err != nil {
		return h.failure(c, err)
	}
	return c.JSON(status, result)
}

// remove admits a DELETE without reading its body.
func (h *SubscriptionMembershipsHandler) remove(c echo.Context, identity domain.ResolvedIdentity,
	remove func(ctx context.Context, identity domain.ResolvedIdentity, audit domain.LoginAuditContext) error) error {
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
	if err := remove(ctx, identity, auditContext); err != nil {
		return h.failure(c, err)
	}
	return c.NoContent(http.StatusNoContent)
}

func (h *SubscriptionMembershipsHandler) Add(c echo.Context) error {
	return h.write(c, http.MethodPost, "subscription_membership_add", subscriptionMembershipAddActions,
		func(ctx context.Context, identity domain.ResolvedIdentity, write *restWrite) (int, any, error) {
			item, err := h.memberships.Add(ctx, identity, h.request(c), write.Body, write.Form, write.Audit)
			return http.StatusCreated, item, err
		},
		func(ctx context.Context, identity domain.ResolvedIdentity, _ domain.LoginAuditContext) error {
			return h.memberships.DeleteWithoutPK(ctx, identity, h.request(c))
		})
}

func (h *SubscriptionMembershipsHandler) Update(c echo.Context) error {
	pk := c.Param("pk")
	return h.write(c, http.MethodPatch, "subscription_membership_update", nil,
		func(ctx context.Context, identity domain.ResolvedIdentity, write *restWrite) (int, any, error) {
			item, err := h.memberships.Update(ctx, identity, pk, h.request(c), write.Body, write.Form, write.Audit)
			return http.StatusOK, item, err
		},
		func(ctx context.Context, identity domain.ResolvedIdentity, audit domain.LoginAuditContext) error {
			return h.memberships.Delete(ctx, identity, pk, h.request(c), audit)
		})
}

func (h *SubscriptionMembershipsHandler) Delete(c echo.Context) error {
	pk := c.Param("pk")
	return h.write(c, http.MethodDelete, "subscription_membership_delete", nil, nil,
		func(ctx context.Context, identity domain.ResolvedIdentity, audit domain.LoginAuditContext) error {
			return h.memberships.Delete(ctx, identity, pk, h.request(c), audit)
		})
}
