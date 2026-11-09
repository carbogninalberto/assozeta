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

type InvoicePartyService interface {
	CustomerList(context.Context, service.InvoicePartyRequest) (map[string]any, error)
	CustomerAdd(context.Context, service.InvoicePartyRequest) error
	CustomerUpdate(context.Context, service.InvoicePartyRequest, string) error
	SupplierList(context.Context, service.InvoicePartyRequest) (map[string]any, error)
	SupplierAdd(context.Context, service.InvoicePartyRequest) error
	SupplierUpdate(context.Context, service.InvoicePartyRequest, string) error
}

type InvoicePartiesHandler struct {
	parties    InvoicePartyService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewInvoicePartiesHandler(parties InvoicePartyService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *InvoicePartiesHandler {
	return &InvoicePartiesHandler{parties: parties, actors: actors, identities: identities, audit: audit, operations: operations}
}

// invoicePartyMessages renders serializer.errors codes: the invoice-party
// codes here, the rest through the payment-category table.
func invoicePartyMessages(request *http.Request, value any) any {
	english := restLanguage(request) == "en"
	switch value := value.(type) {
	case dto.InvoicePartyErrors:
		return invoicePartyMessages(request, map[string]any(value))
	case map[string]any:
		result := make(map[string]any, len(value))
		for field, item := range value {
			result[field] = invoicePartyMessages(request, item)
		}
		return result
	case []string:
		result := make([]string, 0, len(value))
		for _, code := range value {
			switch {
			case strings.HasPrefix(code, dto.InvoicePartyText):
				result = append(result, strings.TrimPrefix(code, dto.InvoicePartyText))
			case code == dto.InvoicePartyUniqueTogether:
				if english {
					result = append(result, "The fields prefix, number, fiscal_year, sport_association must make a unique set.")
				} else {
					result = append(result, "I campi prefix, number, fiscal_year, sport_association devono costituire un insieme unico.")
				}
			case strings.HasPrefix(code, dto.InvoicePartyMaxValue):
				limit := strings.TrimPrefix(code, dto.InvoicePartyMaxValue)
				if english {
					result = append(result, "Ensure this value is less than or equal to "+limit+".")
				} else {
					result = append(result, "Assicurati che il valore sia minore o uguale a "+limit+".")
				}
			default:
				result = append(result, paymentCategoryValidationMessage(code, english))
			}
		}
		return result
	}
	return value
}

func (h *InvoicePartiesHandler) answer(c echo.Context, status int, body any, err error) error {
	request := c.Request()
	var invalid *dto.InvoicePartyValidationError
	var response *domain.InvoicePartyResponseError
	switch {
	case errors.As(err, &invalid):
		return c.JSON(400, invoicePartyMessages(request, invalid.Errors))
	case errors.As(err, &response):
		return c.JSON(response.Status, map[string]string{"error": response.Message})
	case errors.Is(err, domain.ErrInvoicePartyNotAssociation):
		// PermissionDenied with the property's (untranslated) message.
		return restError(c, &domain.PermissionDenied{Detail: domain.ErrInvoicePartyNotAssociation.Error()})
	case errors.Is(err, domain.ErrInvoicePartyForeignReference):
		return restError(c, &domain.PermissionDenied{Detail: messageReadsPermissionDetail(request)})
	case errors.Is(err, dto.ErrDRFInvalidPage):
		detail := "Invalid page."
		if restLanguage(request) != "en" {
			detail = "Pagina non valida."
		}
		return c.JSON(404, map[string]string{"detail": detail})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(status, body)
}

func (h *InvoicePartiesHandler) read(c echo.Context, operation, name string,
	run func(context.Context, service.InvoicePartyRequest) (map[string]any, error)) error {
	identity, err := messageReadsIdentity(c, h.actors, h.identities, http.MethodGet, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, name, "")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	body, err := run(request.Context(), service.InvoicePartyRequest{Identity: *identity, Query: parseReferenceForm(request.URL.RawQuery),
		Group: request.Header.Get("X-Group-Id")})
	return h.answer(c, 200, body, err)
}

func (h *InvoicePartiesHandler) write(c echo.Context, method, operation, name, message string,
	run func(context.Context, service.InvoicePartyRequest) error) error {
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
	admitted, release, err := admitRESTWrite(c, h.operations, h.audit)
	if admitted == nil {
		return err
	}
	defer release()
	err = run(admitted.Context, service.InvoicePartyRequest{Identity: *identity, Body: admitted.Body, Form: admitted.Form,
		Group: request.Header.Get("X-Group-Id"), Audit: admitted.Audit, Authorization: requestAuthorization(request)})
	return h.answer(c, 200, map[string]string{"message": message}, err)
}

func (h *InvoicePartiesHandler) CustomerList(c echo.Context) error {
	return h.read(c, "invoice_customers_list", "Invoice Customers List", h.parties.CustomerList)
}

func (h *InvoicePartiesHandler) SupplierList(c echo.Context) error {
	return h.read(c, "invoice_suppliers_list", "Invoice Suppliers List", h.parties.SupplierList)
}

func (h *InvoicePartiesHandler) CustomerAdd(c echo.Context) error {
	return h.write(c, http.MethodPost, "invoice_customers_add", "Invoice Customers Add", "Invoice created", h.parties.CustomerAdd)
}

func (h *InvoicePartiesHandler) SupplierAdd(c echo.Context) error {
	return h.write(c, http.MethodPost, "invoice_suppliers_add", "Invoice Suppliers Add", "Invoice created", h.parties.SupplierAdd)
}

func (h *InvoicePartiesHandler) CustomerUpdate(c echo.Context) error {
	uid := c.Param("uid")
	return h.write(c, http.MethodPatch, "invoice_customers_update", "Invoice Customers Update", "Invoice updated",
		func(ctx context.Context, request service.InvoicePartyRequest) error {
			return h.parties.CustomerUpdate(ctx, request, uid)
		})
}

func (h *InvoicePartiesHandler) SupplierUpdate(c echo.Context) error {
	uid := c.Param("uid")
	return h.write(c, http.MethodPatch, "invoice_suppliers_update", "Invoice Suppliers Update", "Invoice updated",
		func(ctx context.Context, request service.InvoicePartyRequest) error {
			return h.parties.SupplierUpdate(ctx, request, uid)
		})
}
