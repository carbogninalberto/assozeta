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

type SubscriptionCreateService interface {
	Add(context.Context, domain.ResolvedIdentity, service.SubscriptionCreateRequest) (*service.SubscriptionCreateResult, error)
}

// SubscriptionCreateHandler serves subscription/add (@api_view(['POST']),
// AllowAny); see subscription-create.md for the paths that are refused.
type SubscriptionCreateHandler struct {
	creates    SubscriptionCreateService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewSubscriptionCreateHandler(creates SubscriptionCreateService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *SubscriptionCreateHandler {
	return &SubscriptionCreateHandler{creates: creates, actors: actors, identities: identities, audit: audit, operations: operations}
}

const subscriptionAddDescription = "API endpoint to add a new subscription\n:param request: data: {newUserAccount, newAssociate, signature, paymentMethod}\n" +
	":return: token, refresh token"

func subscriptionCreateMessage(code string, english bool) string {
	if code == dto.SubscriptionCreateDateFormat {
		if english {
			return "Date has wrong format. Use one of these formats instead: DD/MM/YYYY."
		}
		return "La data è in un formato errato. Usa uno dei seguenti formati: DD/MM/YYYY."
	}
	if code == dto.SubscriptionCreateBoolean {
		return "Must be a valid boolean."
	}
	return paymentCategoryValidationMessage(code, english)
}

func (h *SubscriptionCreateHandler) Add(c echo.Context) error {
	identity, err := messageMutationsIdentityFor(c, h.actors, h.identities, http.MethodPost, "subscription_add", true)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Subscription Add", subscriptionAddDescription)
	}
	if request.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	input := service.SubscriptionCreateRequest{Body: write.Body, Form: write.Form, Group: request.Header.Get("X-Group-Id"), Audit: write.Audit,
		Language: djangoReadRouteLocale{}.Language(request)}
	if values := request.URL.Query()["m"]; len(values) > 0 {
		input.Mode = &values[len(values)-1]
	}
	if values, present := request.Header[http.CanonicalHeaderKey("Authorization")]; present && len(values) > 0 {
		value := values[0]
		input.Authorization = &value
	}
	result, err := h.creates.Add(write.Context, *identity, input)
	var invalid *domain.SubscriptionCreateInvalid
	var denied *domain.PermissionDenied
	switch {
	case errors.As(err, &invalid):
		if invalid.Detail != nil {
			return c.JSON(400, invalid.Detail)
		}
		english := restLanguage(request) == "en"
		body := make(map[string][]string, len(invalid.Fields))
		for name, codes := range invalid.Fields {
			messages := make([]string, 0, len(codes))
			for _, code := range codes {
				messages = append(messages, subscriptionCreateMessage(code, english))
			}
			body[name] = messages
		}
		return c.JSON(400, body)
	case errors.Is(err, service.ErrSubscriptionCreateSignatureInvalid):
		return c.JSON(400, map[string]string{"msg": "Firma non valida.", "code": "400", "ref": "invalid_signature"})
	case errors.Is(err, service.ErrSubscriptionCreateStorage):
		return c.JSON(503, map[string]string{"msg": "Storage temporaneamente non disponibile.", "code": "503", "ref": "signature_storage"})
	case errors.Is(err, domain.ErrSubscriptionCreateDuplicate):
		return c.JSON(409, map[string]string{"msg": "Questa iscrizione esiste già.", "code": "409", "ref": "subscription_duplicate"})
	case errors.As(err, &denied):
		return restError(c, denied)
	case err != nil:
		return messageReadsError(c, err)
	}
	payment := "null"
	if result.PaymentID != nil {
		payment = `"` + *result.PaymentID + `"`
	}
	var body strings.Builder
	body.WriteString(`{"status":"success","payment_id":`)
	body.WriteString(payment)
	if result.Amount != "" {
		// The quick re-subscription answers without an amount.
		body.WriteString(`,"amount":`)
		body.WriteString(result.Amount)
	}
	body.WriteString(`}`)
	return c.JSONBlob(200, []byte(body.String()))
}
