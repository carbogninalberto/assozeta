package handler

import (
	"context"
	"errors"
	"net/http"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type PaymentWritesService interface {
	Add(ctx context.Context, identity domain.ResolvedIdentity, request service.PaymentWritesRequest, body any) (map[string]any, error)
	Update(ctx context.Context, identity domain.ResolvedIdentity, request service.PaymentWritesRequest, body any) (map[string]any, error)
	BulkAdd(ctx context.Context, identity domain.ResolvedIdentity, request service.PaymentWritesRequest, body any) (int, map[string]any, error)
}

// PaymentWritesHandler serves the @api_view function views payment_add,
// payment_update and payment_bulk_add.
type PaymentWritesHandler struct {
	payments   PaymentWritesService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewPaymentWritesHandler(payments PaymentWritesService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *PaymentWritesHandler {
	return &PaymentWritesHandler{payments: payments, actors: actors, identities: identities, audit: audit, operations: operations}
}

// paymentEntryErrorsRepr is f"validation errors: {serializer.errors}".
func paymentEntryErrorsRepr(request *http.Request, invalid *dto.PaymentEntryValidationError) string {
	english := restLanguage(request) == "en"
	details := func(codes []string) string {
		parts := make([]string, 0, len(codes))
		for _, code := range codes {
			parts = append(parts, "ErrorDetail(string="+dto.PythonStringRepr(paymentCategoryValidationMessage(code, english))+
				", code="+dto.PythonStringRepr(dto.PaymentEntryDRFCode(code))+")")
		}
		return "[" + strings.Join(parts, ", ") + "]"
	}
	fields := make([]string, 0, len(invalid.Fields))
	for _, field := range invalid.Fields {
		value := details(field.Codes)
		if field.Items != nil {
			items := make([]string, 0, len(field.Items))
			for _, item := range field.Items {
				items = append(items, strconv.Itoa(item.Index)+": "+details(item.Codes))
			}
			value = "{" + strings.Join(items, ", ") + "}"
		}
		fields = append(fields, dto.PythonStringRepr(field.Name)+": "+value)
	}
	return "validation errors: {" + strings.Join(fields, ", ") + "}"
}

// paymentEntryErrors is serializer.errors as raise_exception renders it.
func paymentEntryErrors(request *http.Request, invalid *dto.PaymentEntryValidationError) map[string]any {
	english := restLanguage(request) == "en"
	messages := func(codes []string) []string {
		out := make([]string, 0, len(codes))
		for _, code := range codes {
			out = append(out, paymentCategoryValidationMessage(code, english))
		}
		return out
	}
	result := map[string]any{}
	for _, field := range invalid.Fields {
		if field.Items != nil {
			items := map[string]any{}
			for _, item := range field.Items {
				items[strconv.Itoa(item.Index)] = messages(item.Codes)
			}
			result[field.Name] = items
			continue
		}
		result[field.Name] = messages(field.Codes)
	}
	return result
}

func (h *PaymentWritesHandler) failure(c echo.Context, err error, repr bool) error {
	request := c.Request()
	var denied *domain.PermissionDenied
	var message *domain.PaymentWritesMessage
	var invalid *dto.PaymentEntryValidationError
	var uuid *dto.MessageReadUUIDError
	switch {
	case errors.As(err, &denied):
		return restError(c, err)
	case errors.Is(err, domain.ErrPaymentWritesForeignReference):
		return restError(c, &domain.PermissionDenied{Detail: messageReadsPermissionDetail(request)})
	case errors.Is(err, domain.ErrPaymentWritesAthlete):
		return c.NoContent(http.StatusForbidden)
	case errors.As(err, &message):
		return c.JSON(400, map[string]string{"msg": message.Message})
	case errors.As(err, &invalid):
		if repr {
			return c.JSON(400, map[string]string{"msg": paymentEntryErrorsRepr(request, invalid)})
		}
		return c.JSON(400, paymentEntryErrors(request, invalid))
	case errors.Is(err, domain.ErrPaymentWritesAttachment):
		return c.JSON(400, map[string]string{"exception": "document key not present"})
	case errors.As(err, &uuid):
		return messageReadsError(c, err)
	}
	// Every other failure (including refused, not ported paths) is the 500 page.
	return restError(c, domain.ErrPaymentWritesServer)
}

func (h *PaymentWritesHandler) serve(c echo.Context, verb, operation, name, description string, repr bool,
	run func(ctx context.Context, identity domain.ResolvedIdentity, request service.PaymentWritesRequest, body any) (int, any, error)) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, verb, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	switch request.Method {
	case http.MethodOptions:
		return supplierOptions(c, name, description)
	case verb:
	default:
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	if c.Param("uid") != "" {
		// is_valid_uuid runs before request.data is parsed.
		if _, err := service.PaymentStateUUID(c.Param("uid")); err != nil {
			return h.failure(c, err, repr)
		}
	}
	admitted, release, err := admitRESTWrite(c, h.operations, h.audit)
	if admitted == nil {
		return err
	}
	defer release()
	status, result, err := run(admitted.Context, *identity, service.PaymentWritesRequest{ID: c.Param("uid"),
		GroupHeader: request.Header.Get("X-Group-Id"), Form: admitted.Form, Audit: admitted.Audit,
		Authorization: requestAuthorization(request)}, admitted.Body)
	if err != nil {
		return h.failure(c, err, repr)
	}
	return c.JSON(status, result)
}

const paymentWritesDescription = "API endpoint to add a new payment"

func (h *PaymentWritesHandler) Add(c echo.Context) error {
	return h.serve(c, http.MethodPost, "payment_add", "Payment Add", paymentWritesDescription, true,
		func(ctx context.Context, identity domain.ResolvedIdentity, request service.PaymentWritesRequest, body any) (int, any, error) {
			result, err := h.payments.Add(ctx, identity, request, body)
			return http.StatusCreated, result, err
		})
}

func (h *PaymentWritesHandler) Update(c echo.Context) error {
	return h.serve(c, http.MethodPatch, "payment_update", "Payment Update", paymentWritesDescription, false,
		func(ctx context.Context, identity domain.ResolvedIdentity, request service.PaymentWritesRequest, body any) (int, any, error) {
			result, err := h.payments.Update(ctx, identity, request, body)
			return http.StatusOK, result, err
		})
}

func (h *PaymentWritesHandler) BulkAdd(c echo.Context) error {
	return h.serve(c, http.MethodPost, "payment_bulk_add", "Payment Bulk Add",
		"API endpoint to add multiple payments for multiple associates in a single operation.", false,
		func(ctx context.Context, identity domain.ResolvedIdentity, request service.PaymentWritesRequest, body any) (int, any, error) {
			return h.payments.BulkAdd(ctx, identity, request, body)
		})
}
