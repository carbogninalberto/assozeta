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

type PaymentCategoryService interface {
	AdmitAdd(context.Context, domain.ResolvedIdentity) error
	AdmitUpdate(context.Context, domain.ResolvedIdentity, string) error
	List(context.Context, domain.ResolvedIdentity) ([]dto.PaymentCategory, error)
	Add(context.Context, domain.ResolvedIdentity, dto.PaymentCategoryWrite, domain.LoginAuditContext) (dto.PaymentCategory, error)
	Update(context.Context, domain.ResolvedIdentity, string, dto.PaymentCategoryWrite, domain.LoginAuditContext) (dto.PaymentCategory, error)
	Delete(context.Context, domain.ResolvedIdentity, string, domain.LoginAuditContext) error
}

type PaymentCategoriesHandler struct {
	categories PaymentCategoryService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewPaymentCategoriesHandler(categories PaymentCategoryService, actors *service.ActorAuthentication, identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *PaymentCategoriesHandler {
	return &PaymentCategoriesHandler{categories: categories, actors: actors, identities: identities, audit: audit, operations: operations}
}

func (h *PaymentCategoriesHandler) List(c echo.Context) error {
	identity, err := authenticatedRESTIdentity(c, h.actors, h.identities, http.MethodGet, "payment_category_list")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return paymentCategoryOptions(c, "Payment Category List")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	result, err := h.categories.List(request.Context(), *identity)
	if err != nil {
		return paymentCategoryError(c, err)
	}
	return c.JSON(200, map[string]any{"data": result})
}

func (h *PaymentCategoriesHandler) Add(c echo.Context) error {
	identity, err := authenticatedRESTIdentityBody(c, h.actors, h.identities, http.MethodPost, "payment_category_add", readPaymentCategoryBody)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return paymentCategoryOptions(c, "Payment Category Add")
	}
	if request.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	operation, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer operation.Release()
	if err = h.categories.AdmitAdd(operation.Context(), *identity); err != nil {
		return paymentCategoryError(c, err)
	}
	body, err := paymentCategoryWriteBody(c, false)
	if err != nil {
		return restError(c, err)
	}
	input, err := dto.ParsePaymentCategoryWrite(body, false)
	var validation *dto.PaymentCategoryValidationError
	if err != nil && !errors.As(err, &validation) {
		return paymentCategoryError(c, err)
	}
	audit, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	operationContext, err := operation.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	result, err := h.categories.Add(operationContext, *identity, input, audit)
	if err != nil {
		return paymentCategoryError(c, err)
	}
	return c.JSON(200, map[string]any{"data": result, "msg": "payment category created"})
}

func (h *PaymentCategoriesHandler) Update(c echo.Context) error {
	identity, err := authenticatedRESTIdentityBody(c, h.actors, h.identities, http.MethodPatch, "payment_category_update", readPaymentCategoryBody)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return paymentCategoryOptions(c, "Payment Category Update")
	}
	if request.Method != http.MethodPatch {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	operation, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer operation.Release()
	id := c.Param("uid")
	if err = h.categories.AdmitUpdate(operation.Context(), *identity, id); err != nil {
		return paymentCategoryError(c, err)
	}
	body, err := paymentCategoryWriteBody(c, true)
	if err != nil {
		return restError(c, err)
	}
	input, err := dto.ParsePaymentCategoryWrite(body, true)
	var validation *dto.PaymentCategoryValidationError
	if err != nil && !errors.As(err, &validation) {
		return paymentCategoryError(c, err)
	}
	audit, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	operationContext, err := operation.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	result, err := h.categories.Update(operationContext, *identity, id, input, audit)
	if err != nil {
		return paymentCategoryError(c, err)
	}
	return c.JSON(200, map[string]any{"data": result, "msg": "payment category updated"})
}

func (h *PaymentCategoriesHandler) Delete(c echo.Context) error {
	identity, err := authenticatedRESTIdentity(c, h.actors, h.identities, http.MethodDelete, "payment_category_delete")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return paymentCategoryOptions(c, "Payment Category Delete")
	}
	if request.Method != http.MethodDelete {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	operation, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer operation.Release()
	id := c.Param("uid")
	audit, err := h.audit(request)
	if err != nil {
		return restError(c, err)
	}
	operationContext, err := operation.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	if err = h.categories.Delete(operationContext, *identity, id, audit); err != nil {
		return paymentCategoryError(c, err)
	}
	return c.JSON(200, map[string]string{"msg": "payment category deleted"})
}

func readPaymentCategoryBody(c echo.Context) (any, error) {
	// Reuse the existing Python-code-unit parser so name validation can reject
	// lone surrogates while retaining valid pairs and literal U+FFFD.
	return readAuthenticatedRESTBodyUsing(c, func(request *http.Request) (any, error) {
		return readReferenceBodyJSON(request, dto.ParseProfileTablesJSON)
	})
}

func paymentCategoryWriteBody(c echo.Context, partial bool) (any, error) {
	body, err := readPaymentCategoryBody(c)
	if err != nil {
		return nil, err
	}
	media, _, _ := mime.ParseMediaType(c.Request().Header.Get("Content-Type"))
	if media != "application/x-www-form-urlencoded" && media != "multipart/form-data" {
		return body, nil
	}
	fields, ok := body.(domain.RequestObject)
	if !ok {
		return body, nil
	}
	files := false
	for _, field := range fields {
		if _, ok := field.Value.(domain.RequestUpload); ok {
			files = true
		}
	}
	// The legacy CREATE view assigns its association into request.data. DRF
	// keeps a QueryDict immutable unless multipart files caused a mutable copy.
	if !partial && !files {
		return nil, errors.New("payment category CREATE received immutable form data")
	}
	result, vat := domain.RequestObject{}, domain.RequestObject{}
	for _, field := range fields {
		if strings.HasPrefix(field.Name, "vat_management.") {
			vat = append(vat, domain.RequestField{Name: strings.TrimPrefix(field.Name, "vat_management."), Value: field.Value})
			continue
		}
		if field.Name == "vat_management" {
			continue // Nested serializers read dotted HTML keys, not this value.
		}
		if field.Name == "sport_association" && field.Value == "" {
			result = append(result, domain.RequestField{Name: field.Name, Value: nil})
			continue
		}
		if field.Name != "name" && field.Value == "" {
			continue // Optional HTML fields use DRF's empty sentinel.
		}
		result = append(result, field)
	}
	if len(vat) > 0 {
		nested := domain.RequestObject{}
		for _, field := range vat {
			if field.Value != "" {
				nested = append(nested, field)
			}
		}
		result = append(result, domain.RequestField{Name: "vat_management", Value: nested})
	}
	return result, nil
}

func paymentCategoryOptions(c echo.Context, name string) error {
	return c.JSON(200, map[string]any{
		"name": name, "description": "",
		"renders": []string{"application/json"},
		"parses":  []string{"application/json", "application/x-www-form-urlencoded", "multipart/form-data"},
	})
}

func paymentCategoryError(c echo.Context, err error) error {
	var validation *dto.PaymentCategoryValidationError
	switch {
	case errors.As(err, &validation):
		return c.JSON(400, paymentCategoryValidationMessages(c.Request(), validation.Errors))
	case errors.Is(err, domain.ErrPaymentCategoryListForbidden):
		return c.JSON(403, map[string]string{"error": "user not allowed."})
	case errors.Is(err, domain.ErrPaymentCategoryForbidden):
		return c.JSON(403, map[string]string{"error": "user not authorized"})
	case errors.Is(err, domain.ErrAssociationNotFound):
		return c.JSON(403, map[string]string{"error": "user not authorized"})
	case errors.Is(err, domain.ErrPaymentCategoryNotFound):
		return c.JSON(404, map[string]string{"error": "payment category not found"})
	case errors.Is(err, domain.ErrPaymentCategoryInvalidID):
		return restError(c, err)
	case errors.Is(err, domain.ErrPaymentCategoryVATRequired):
		return c.JSON(400, paymentCategoryValidationMessages(c.Request(), map[string]any{
			"vat_management": []string{dto.PaymentCategoryValidationRequired},
		}))
	default:
		return restError(c, err)
	}
}

func paymentCategoryValidationMessages(request *http.Request, value any) any {
	english := restLanguage(request) == "en"
	switch value := value.(type) {
	case map[string]any:
		result := make(map[string]any, len(value))
		for field, message := range value {
			result[field] = paymentCategoryValidationMessages(request, message)
		}
		return result
	case []string:
		result := make([]string, 0, len(value))
		for _, code := range value {
			result = append(result, paymentCategoryValidationMessage(code, english))
		}
		return result
	default:
		return value
	}
}

func paymentCategoryValidationMessage(code string, english bool) string {
	switch {
	case code == dto.PaymentCategoryValidationRequired:
		if english {
			return "This field is required."
		}
		return "Campo obbligatorio."
	case code == dto.PaymentCategoryValidationNull:
		if english {
			return "This field may not be null."
		}
		return "Il campo non può essere nullo."
	case code == dto.PaymentCategoryValidationBlank:
		if english {
			return "This field may not be blank."
		}
		return "Questo campo non può essere omesso."
	case code == dto.PaymentCategoryValidationUUID:
		return "Must be a valid UUID."
	case code == dto.PaymentCategoryValidationString:
		return "Not a valid string."
	case code == dto.PaymentCategoryValidationDateTimeOverflow:
		return "Datetime value out of range."
	case code == dto.PaymentCategoryValidationDateTime:
		if english {
			return "Datetime has wrong format. Use one of these formats instead: YYYY-MM-DDThh:mm[:ss[.uuuuuu]][+HH:MM|-HH:MM|Z]."
		}
		return "L'oggetto di tipo datetime è in un formato errato. Usa uno dei seguenti formati: YYYY-MM-DDThh:mm[:ss[.uuuuuu]][+HH:MM|-HH:MM|Z]."
	case code == dto.PaymentCategoryValidationBoolean:
		return "Must be a valid boolean."
	case code == dto.PaymentCategoryValidationDict:
		if english {
			return "Invalid data. Expected a dictionary."
		}
		return "Dati non validi. Era atteso un dizionario."
	case code == dto.PaymentCategoryValidationUniqueVAT:
		if english {
			return "vat management with this vat management id already exists."
		}
		return "vat management con questo vat management id esiste già."
	case code == dto.PaymentCategoryValidationUniqueID:
		if english {
			return "payment category with this payment category id already exists."
		}
		return "payment category con questo payment category id esiste già."
	case code == "no-data":
		return "No data provided"
	case code == "null-character":
		if english {
			return "Null characters are not allowed."
		}
		return "I caratteri null non sono ammessi."
	case strings.HasPrefix(code, "surrogate:"):
		return "Surrogate characters are not allowed: U+" + strings.TrimPrefix(code, "surrogate:") + "."
	case strings.HasPrefix(code, "dictionary:"):
		kind := strings.TrimPrefix(code, "dictionary:")
		if english {
			return "Invalid data. Expected a dictionary, but got " + kind + "."
		}
		return "Dati non validi. Era atteso un dizionario, ma si è ricevuto " + kind + "."
	case code == "immutable-category-id":
		return "L'identificativo della categoria non può essere modificato."
	case code == "immutable-association":
		return "L'associazione della categoria non può essere modificata."
	case strings.HasPrefix(code, "related-does-not-exist:"):
		id := strings.TrimPrefix(code, "related-does-not-exist:")
		if english {
			return `Invalid pk "` + id + `" - object does not exist.`
		}
		return `Pk "` + id + `" non valido - l'oggetto non esiste.`
	case strings.HasPrefix(code, "max:"):
		limit := strings.TrimPrefix(code, "max:")
		if english {
			return "Ensure this field has no more than " + limit + " characters."
		}
		return "Assicurati che questo campo non abbia più di " + limit + " caratteri."
	case strings.HasPrefix(code, "choice:"):
		choice := strings.TrimPrefix(code, "choice:")
		if english {
			return `"` + choice + `" is not a valid choice.`
		}
		return `"` + choice + `" non è una scelta valida.`
	default:
		return code
	}
}
