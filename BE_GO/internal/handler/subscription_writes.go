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

type SubscriptionEditService interface {
	Edit(ctx context.Context, identity domain.ResolvedIdentity, id string, body any, form bool, group string, audit domain.LoginAuditContext) error
}

// SubscriptionWritesHandler serves subscription/<uid>/edit (@api_view(['POST'])).
// subscription/add is SubscriptionCreateHandler, subscription/renew is
// SubscriptionRenewHandler; subscription/<uid>/transfer is not routed
// (subscription-create.md, subscription-renew.md).
type SubscriptionWritesHandler struct {
	edits      SubscriptionEditService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewSubscriptionWritesHandler(edits SubscriptionEditService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *SubscriptionWritesHandler {
	return &SubscriptionWritesHandler{edits: edits, actors: actors, identities: identities, audit: audit, operations: operations}
}

// associateTutorErrors renders serializer.errors.
func associateTutorErrors(request *http.Request, invalid *dto.AssociateTutorInvalid) map[string][]string {
	english := restLanguage(request) == "en"
	result := make(map[string][]string, len(invalid.Fields))
	for _, field := range invalid.Fields {
		messages := make([]string, 0, len(field.Codes))
		for _, code := range field.Codes {
			messages = append(messages, paymentCategoryValidationMessage(code, english))
		}
		result[field.Name] = messages
	}
	return result
}

func (h *SubscriptionWritesHandler) Edit(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "subscription_edit")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Subscription Edit", "this endpoint temporalily is used to update the associate tutor data")
	}
	if request.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	// is_valid_uuid runs before request.data is parsed.
	id, err := service.SubscriptionEditID(c.Param("uid"))
	var malformed *service.PaymentInfoUUIDError
	if errors.As(err, &malformed) {
		return c.JSON(400, []string{malformed.Detail})
	}
	if err != nil {
		return messageReadsError(c, err)
	}
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	err = h.edits.Edit(write.Context, *identity, id, write.Body, write.Form, request.Header.Get("X-Group-Id"), write.Audit)
	var invalid *dto.AssociateTutorInvalid
	var rejected *service.SubscriptionEditRejected
	switch {
	case errors.As(err, &invalid):
		return c.JSON(400, associateTutorErrors(request, invalid))
	case errors.As(err, &rejected):
		return c.JSON(400, []string{rejected.Detail})
	case errors.Is(err, domain.ErrSubscriptionEditDenied):
		return restError(c, &domain.PermissionDenied{Detail: "User not allowed."})
	case errors.Is(err, domain.ErrSubscriptionEditForeignReference):
		return restError(c, &domain.PermissionDenied{Detail: messageReadsPermissionDetail(request)})
	case err != nil:
		return messageReadsError(c, err)
	}
	return c.JSON(200, map[string]any{"data": map[string]string{"msg": "subscription associate edited."}})
}
