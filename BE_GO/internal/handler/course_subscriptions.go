package handler

import (
	"context"
	"errors"
	"net/http"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type CourseSubscriptionService interface {
	List(ctx context.Context, identity domain.ResolvedIdentity, request service.CourseSubscriptionRequest, export bool) (map[string]any, error)
	DeleteWithoutPK(ctx context.Context, identity domain.ResolvedIdentity) error
	Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, request service.CourseSubscriptionRequest, body any, form bool,
		audit domain.LoginAuditContext) (map[string]any, error)
	Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, includeDeleted bool, request service.CourseSubscriptionRequest,
		audit domain.LoginAuditContext) error
	BulkDelete(ctx context.Context, identity domain.ResolvedIdentity, request service.CourseSubscriptionRequest, body any,
		audit domain.LoginAuditContext) error
}

type CourseSubscriptionsHandler struct {
	subscriptions CourseSubscriptionService
	actors        *service.ActorAuthentication
	identities    *service.IdentityResolver
	audit         LoginAuditReader
	operations    *paymentcategorylifecycle.Lifecycle
}

func NewCourseSubscriptionsHandler(subscriptions CourseSubscriptionService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *CourseSubscriptionsHandler {
	return &CourseSubscriptionsHandler{subscriptions: subscriptions, actors: actors, identities: identities, audit: audit, operations: operations}
}

// The scoped-request guard names viewsets by class.
const courseSubscriptionOperation = "CourseSubscriptionViewSet"

func courseSubscriptionField(kind, label string, required, readOnly bool, extra map[string]any) map[string]any {
	value := map[string]any{"type": kind, "required": required, "read_only": readOnly}
	if label != "" {
		value["label"] = label
	}
	for key, item := range extra {
		value[key] = item
	}
	return value
}

func courseSubscriptionText(label string, size int, required bool) map[string]any {
	var extra map[string]any
	if size > 0 {
		extra = map[string]any{"max_length": size}
	}
	return courseSubscriptionField("string", label, required, false, extra)
}

func courseSubscriptionNested(label string, children map[string]any, required, readOnly bool) map[string]any {
	return courseSubscriptionField("nested object", label, required, readOnly, map[string]any{"children": children})
}

func courseSubscriptionChoices(pairs ...[2]any) []map[string]any {
	result := make([]map[string]any, 0, len(pairs))
	for _, pair := range pairs {
		result = append(result, map[string]any{"value": pair[0], "display_name": pair[1]})
	}
	return result
}

// courseSubscriptionListActions is SimpleMetadata's POST description of
// CourseSubscriptionOverviewSerializer (the list route allows POST).
var courseSubscriptionListActions = func() map[string]any {
	f, t, n := courseSubscriptionField, courseSubscriptionText, courseSubscriptionNested
	plain := func(kind, label string) map[string]any { return f(kind, label, false, false, nil) }
	readOnly := func(label string) map[string]any { return f("field", label, false, true, nil) }
	limits := func(kind, label string, extra map[string]any) map[string]any {
		return f(kind, label, false, false, extra)
	}
	decimal := func(label string, digits, places int, required bool) map[string]any {
		return f("decimal", label, required, false, map[string]any{"max_digits": digits, "decimal_places": places})
	}
	associate := map[string]any{
		"associate_id": t("Associate id", 0, false), "address": t("Address", 150, false), "address_cap": t("Address cap", 5, false),
		"address_city": t("Address city", 150, false), "born_city": t("Born city", 150, false), "born_date": plain("date", "Born date"),
		"email":      limits("email", "Email", map[string]any{"max_length": 254}),
		"sex":        limits("choice", "Sex", map[string]any{"choices": courseSubscriptionChoices([2]any{"A", "other"}, [2]any{"M", "male"}, [2]any{"F", "female"})}),
		"first_name": t("First name", 150, false), "last_name": t("Last name", 150, false), "is_minor": plain("boolean", "Is minor"),
		"sport_association": plain("field", "Sport association"), "tax_code": t("Tax code", 25, false), "user": readOnly("User"),
		"phone": t("Phone", 30, false), "family": plain("field", "Family"), "family_role": t("Family role", 100, false)}
	user := map[string]any{"first_name": t("First name", 150, false), "last_name": t("Last name", 150, false),
		"email": limits("email", "Email address", map[string]any{"max_length": 254}), "phone": t("Phone", 20, false),
		"username": t("Username", 0, false),
		"role": limits("choice", "Role", map[string]any{"choices": courseSubscriptionChoices([2]any{1, "association"}, [2]any{2, "athlete"},
			[2]any{3, "association"})})}
	instructor := map[string]any{
		"instructor_id": t("Instructor id", 0, false), "first_name": t("First name", 150, true), "last_name": t("Last name", 150, true),
		"email": limits("email", "Email", map[string]any{"max_length": 254}), "born_date": plain("date", "Born date"),
		"born_city": t("Born city", 150, false), "born_province": t("Born province", 150, false), "tax_code": t("Tax code", 25, false),
		"address_city": t("Address city", 150, false), "address": t("Address", 150, false), "address_province": t("Address province", 150, false),
		"civic_number": t("Civic number", 10, false), "study_title": t("Study title", 150, false), "role": t("Role", 150, false),
		"phone": t("Phone", 20, false), "phone_2": t("Phone 2", 20, false), "phone_3": t("Phone 3", 20, false), "phone_4": t("Phone 4", 20, false),
		"draft": plain("boolean", "Draft"), "associated_user_id": t("Associated user id", 0, false), "is_volunteer": plain("boolean", "Is volunteer"),
		"stipulated_contract_in": plain("date", "Stipulated contract in"), "default_hourly_billing": decimal("Default hourly billing", 9, 2, false),
		"default_percentage_billing": decimal("Default percentage billing", 4, 2, false), "user": f("field", "User", true, false, nil),
		"documents": plain("field", "Documents")}
	supplier := map[string]any{
		"supplier_id": t("Supplier id", 0, false), "name": t("Name", 255, true), "address": t("Address", 255, false), "tax_code": t("Tax code", 30, false),
		"vat_number": t("Vat number", 30, false), "type": t("Type", 30, false), "email": limits("email", "Email", map[string]any{"max_length": 254}),
		"phone_number": t("Phone number", 20, false), "city": t("City", 100, false), "cap": t("Cap", 10, false), "state": t("State", 50, false),
		"country": t("Country", 50, false), "nationality": t("Nationality", 100, false), "note": t("Note", 0, false),
		"deleted": plain("boolean", "Deleted"), "sport_association": plain("field", "Sport association"), "group": plain("field", "Group")}
	invoice := map[string]any{
		"invoice_id": t("Invoice id", 0, false), "document_token": readOnly("Document token"), "creation_date": plain("datetime", "Creation date"),
		"membership_fee": decimal("Membership fee", 9, 2, true), "activity_fee": decimal("Activity fee", 9, 2, true),
		"meta_payment_categories": plain("field", "Meta payment categories"), "description": t("Description", 255, false),
		"number":   limits("integer", "Number", map[string]any{"min_value": -2147483648, "max_value": 2147483647}),
		"archived": plain("boolean", "Archived"), "cancelled": plain("boolean", "Cancelled"), "meta": plain("field", "Meta"),
		"imported_from_associami": plain("boolean", "Imported from associami"), "deleted": plain("boolean", "Deleted"),
		"document_pdf": plain("field", "Document pdf"), "sport_association": f("field", "Sport association", true, false, nil),
		"selected_tutor": plain("field", "Selected tutor"), "group": plain("field", "Group"), "attached_documents": plain("field", "Attached documents")}
	document := map[string]any{"document_id": t("Document id", 0, false), "creation_date": plain("datetime", "Creation date"),
		"filepath": t("Filepath", 1000, false), "filename": t("Filename", 255, true),
		"file_size_bytes": limits("integer", "File size bytes", map[string]any{"min_value": 0, "max_value": int64(9223372036854775807)}),
		"token":           t("Token", 0, false)}
	payment := map[string]any{
		"payment_id": t("Payment id", 0, false), "user": n("User", user, true, false), "associate": n("Associate", associate, true, false),
		"subscription_id": readOnly("Subscription id"), "instructor_id": readOnly("Instructor id"), "instructor": n("Instructor", instructor, true, false),
		"supplier": n("Supplier", supplier, true, false), "supplier_id": readOnly("Supplier id"), "custom_account_type": readOnly("Custom account type"),
		"custom_account_name": readOnly("Custom account name"), "invoice": n("Invoice", invoice, false, false),
		"attachments":           f("field", "Attachments", false, true, map[string]any{"child": n("", document, false, true)}),
		"payment_category_name": readOnly("Payment category name"),
		"type": limits("choice", "Type", map[string]any{"choices": courseSubscriptionChoices([2]any{"default", "DEFAULT"}, [2]any{"cash", "CONTANTI"},
			[2]any{"transfer", "BONIFICO"}, [2]any{"online", "ONLINE"}, [2]any{"sepa-transfer", "BONIFICO SEPA"}, [2]any{"stripe", "STRIPE"},
			[2]any{"pos", "POS"})}),
		"subject": limits("choice", "Subject", map[string]any{"choices": courseSubscriptionChoices([2]any{0, "Altro"}, [2]any{1, "Iscrizione"},
			[2]any{2, "Corso"}, [2]any{3, "Giroconto"})}),
		"amount": decimal("Amount", 9, 2, false), "creation_date": plain("datetime", "Creation date"), "payment_date": plain("datetime", "Payment date"),
		"paid": plain("boolean", "Paid"), "expense": plain("boolean", "Expense"), "description": t("Description", 5120, false),
		"meta_payment_categories": plain("field", "Meta payment categories"), "payment_intent_id": t("Payment intent id", 125, false),
		"archived": plain("boolean", "Archived"), "invoice_fiscal_code": t("Invoice fiscal code", 125, false), "meta": plain("field", "Meta"),
		"notes": t("Notes", 5120, false), "deleted": plain("boolean", "Deleted"), "course": plain("field", "Course"),
		"payment_category": plain("field", "Payment category"), "sport_association": plain("field", "Sport association"),
		"custom_accounts": plain("field", "Custom accounts"), "signature": plain("field", "Signature"), "group": plain("field", "Group")}
	return map[string]any{"POST": map[string]any{
		"course_subscription_id": t("Course subscription id", 0, false), "subscription_id": t("Subscription id", 0, true),
		"creation_date": plain("datetime", "Creation date"), "paid": plain("boolean", "Paid"),
		"subscription": n("Subscription", map[string]any{"subscription_id": t("Subscription id", 0, false),
			"associate": n("Associate", associate, true, false), "current_year": readOnly("Current year")}, false, true),
		"multi_payments": plain("boolean", "Multi payments"), "multiple_quote": plain("field", "Multiple quote"),
		"one_fee_payment": plain("boolean", "One fee payment"), "installments": readOnly("Installments"),
		"course": f("field", "Course", true, false, nil), "type": f("choice", "Type", false, true, nil),
		"billed_frequency": limits("integer", "Billed frequency", map[string]any{"min_value": 0, "max_value": 32767}),
		"billed_from":      plain("datetime", "Billed from"), "billed_until": plain("datetime", "Billed until"),
		"billed_from_day_of_month": limits("integer", "Billed from day of month", map[string]any{"min_value": 1, "max_value": 28}),
		"membership_active":        plain("boolean", "Membership active"), "membership_fee": decimal("Membership fee", 9, 2, false),
		"auto_renewal":        plain("boolean", "Auto renewal"),
		"membership_payments": f("field", "Membership payments", false, true, map[string]any{"child": n("", payment, false, true)}),
		"events":              plain("field", "Events"), "medical_label": readOnly("Medical label"), "carnets": readOnly("Carnets")}}
}()

// courseSubscriptionMessages renders the field errors: the integer max_value
// code here, the shared DRF codes through the payment-category table, and the
// view's plain texts unchanged.
func courseSubscriptionMessages(request *http.Request, fields map[string]any) any {
	english := restLanguage(request) == "en"
	translated := make(map[string]any, len(fields))
	for name, value := range fields {
		if codes, ok := value.([]string); ok {
			messages := make([]string, len(codes))
			for index, code := range codes {
				messages[index] = code
				if limit, found := strings.CutPrefix(code, "max-value:"); found {
					messages[index] = "Assicurati che il valore sia minore o uguale a " + limit + "."
					if english {
						messages[index] = "Ensure this value is less than or equal to " + limit + "."
					}
				}
			}
			value = messages
		}
		translated[name] = value
	}
	return paymentCategoryValidationMessages(request, translated)
}

func (h *CourseSubscriptionsHandler) request(c echo.Context) service.CourseSubscriptionRequest {
	request := c.Request()
	return service.CourseSubscriptionRequest{Query: parseReferenceForm(request.URL.RawQuery), GroupHeader: request.Header.Get("X-Group-Id")}
}

func (h *CourseSubscriptionsHandler) failure(c echo.Context, err error) error {
	request := c.Request()
	var invalid *service.CourseSubscriptionValidationError
	switch {
	case errors.As(err, &invalid):
		return c.JSON(400, courseSubscriptionMessages(request, invalid.Fields))
	case errors.Is(err, domain.ErrCourseSubscriptionNotFound):
		return c.JSON(404, map[string]string{"detail": "No CourseSubscription matches the given query."})
	}
	var denied *domain.PermissionDenied
	if errors.As(err, &denied) {
		return restError(c, err)
	}
	// Every other failure is an unhandled exception in Django: the 500 page.
	return restError(c, domain.ErrCourseSubscriptionServer)
}

func (h *CourseSubscriptionsHandler) options(c echo.Context, actions map[string]any) error {
	if actions == nil {
		return supplierOptions(c, "Course Subscription", "")
	}
	return c.JSON(200, map[string]any{"name": "Course Subscription", "description": "", "renders": []string{"application/json"},
		"parses": []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"}, "actions": actions})
}

// List serves course-subscriptions/list: as_view({'get': 'list', 'post':
// 'list'}) (GET also answers HEAD) plus the viewset's own delete().
func (h *CourseSubscriptionsHandler) List(c echo.Context) error {
	c.Response().Header().Set("Allow", "GET, POST, HEAD, DELETE, OPTIONS")
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, courseSubscriptionOperation)
	if identity == nil {
		return err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return h.options(c, courseSubscriptionListActions)
	case http.MethodGet, http.MethodHead, http.MethodPost:
		if request.Method == http.MethodPost {
			// The export reads request.data (a parse error is DRF's 400).
			if _, err := readMessageMutationsBody(c); err != nil {
				return restError(c, err)
			}
		}
		result, err := h.subscriptions.List(request.Context(), *identity, h.request(c), request.Method == http.MethodPost)
		if err != nil {
			return h.failure(c, err)
		}
		return c.JSON(http.StatusOK, result)
	case http.MethodDelete:
		return h.failure(c, h.subscriptions.DeleteWithoutPK(request.Context(), *identity))
	}
	return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
}

// remove admits a DELETE without reading its body (delete() never reads it).
func (h *CourseSubscriptionsHandler) remove(c echo.Context, identity domain.ResolvedIdentity, includeDeleted bool) error {
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
	if err := h.subscriptions.Delete(ctx, identity, c.Param("pk"), includeDeleted, h.request(c), auditContext); err != nil {
		return h.failure(c, err)
	}
	return c.NoContent(http.StatusNoContent)
}

// Update serves course-subscriptions/<pk>/update ({'patch': 'update'}); the
// viewset's delete() answers DELETE with the live rows only.
func (h *CourseSubscriptionsHandler) Update(c echo.Context) error {
	c.Response().Header().Set("Allow", "PATCH, DELETE, OPTIONS")
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPatch, courseSubscriptionOperation)
	if identity == nil {
		return err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return h.options(c, nil)
	case http.MethodDelete:
		return h.remove(c, *identity, false)
	case http.MethodPatch:
		admitted, release, err := admitRESTWrite(c, h.operations, h.audit)
		if admitted == nil {
			return err
		}
		defer release()
		result, err := h.subscriptions.Update(admitted.Context, *identity, c.Param("pk"), h.request(c), admitted.Body, admitted.Form, admitted.Audit)
		if err != nil {
			return h.failure(c, err)
		}
		return c.JSON(http.StatusOK, result)
	}
	return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
}

// Delete serves course-subscriptions/<pk>/delete: the delete action includes
// soft-deleted rows.
func (h *CourseSubscriptionsHandler) Delete(c echo.Context) error {
	c.Response().Header().Set("Allow", "DELETE, OPTIONS")
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodDelete, courseSubscriptionOperation)
	if identity == nil {
		return err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return h.options(c, nil)
	case http.MethodDelete:
		return h.remove(c, *identity, true)
	}
	return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
}

// BulkDelete serves course-subscriptions/bulk-delete ({'delete': 'bulk_delete'}).
func (h *CourseSubscriptionsHandler) BulkDelete(c echo.Context) error {
	c.Response().Header().Set("Allow", "DELETE, OPTIONS")
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodDelete, courseSubscriptionOperation)
	if identity == nil {
		return err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return h.options(c, nil)
	case http.MethodDelete:
		admitted, release, err := admitRESTWrite(c, h.operations, h.audit)
		if admitted == nil {
			return err
		}
		defer release()
		if err := h.subscriptions.BulkDelete(admitted.Context, *identity, h.request(c), admitted.Body, admitted.Audit); err != nil {
			return h.failure(c, err)
		}
		return c.NoContent(http.StatusNoContent)
	}
	return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
}
