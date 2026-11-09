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

type SubscriptionSignService interface {
	Sign(context.Context, domain.ResolvedIdentity, service.SubscriptionSignRequest) error
}

// SubscriptionSignHandler serves subscription/sign (@api_view(['POST']),
// IsAuthenticated); see subscription-sign.md.
type SubscriptionSignHandler struct {
	signs      SubscriptionSignService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewSubscriptionSignHandler(signs SubscriptionSignService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *SubscriptionSignHandler {
	return &SubscriptionSignHandler{signs: signs, actors: actors, identities: identities, audit: audit, operations: operations}
}

const subscriptionSignDescription = "API endpoint to sign a subscription\n:param request: data: {subscription_id, signature}\n" +
	":return: token, refresh token"

func subscriptionSignMessage(code string, english bool) string {
	switch code {
	case dto.SubscriptionSignatureNoData:
		return "No data provided"
	case dto.SubscriptionSignatureRequired:
		return "La firma è obbligatoria."
	}
	return paymentCategoryValidationMessage(code, english)
}

func (h *SubscriptionSignHandler) Sign(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "subscription_sign")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Subscription Sign", subscriptionSignDescription)
	}
	if request.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	input := service.SubscriptionSignRequest{Body: write.Body, Group: request.Header.Get("X-Group-Id"), Audit: write.Audit}
	if values, present := request.Header[http.CanonicalHeaderKey("Authorization")]; present && len(values) > 0 {
		value := values[0]
		input.Authorization = &value
	}
	err = h.signs.Sign(write.Context, *identity, input)
	var uuidError *service.SubscriptionSignUUIDError
	var fields *service.SubscriptionSignFieldsError
	var denied *domain.PermissionDenied
	switch {
	case err == nil:
		return c.JSON(http.StatusOK, map[string]string{"status": "success"})
	case errors.Is(err, service.ErrSubscriptionSignValidation):
		return c.JSON(http.StatusBadRequest, map[string]string{"msg": "validation errors."})
	case errors.As(err, &uuidError):
		return c.JSON(http.StatusBadRequest, []string{uuidError.Detail})
	case errors.As(err, &fields):
		english := restLanguage(request) == "en"
		body := make(map[string][]string, len(fields.Fields))
		for _, field := range fields.Fields {
			messages := make([]string, 0, len(field.Codes))
			for _, code := range field.Codes {
				messages = append(messages, subscriptionSignMessage(code, english))
			}
			body[field.Name] = messages
		}
		return c.JSON(http.StatusBadRequest, body)
	case errors.Is(err, service.ErrSubscriptionSignInvalid):
		return c.JSON(http.StatusBadRequest, map[string]string{"msg": "Firma non valida.", "code": "400", "ref": "invalid_signature"})
	case errors.Is(err, service.ErrSubscriptionSignStorage):
		return c.JSON(http.StatusServiceUnavailable, map[string]string{"msg": "Storage temporaneamente non disponibile.", "code": "503",
			"ref": "signature_storage"})
	case errors.As(err, &denied):
		return restError(c, denied)
	}
	return messageReadsError(c, err)
}
