package handler

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type SubscriptionRenewService interface {
	Renew(context.Context, domain.ResolvedIdentity, service.SubscriptionCreateRequest) (*service.SubscriptionRenewResult, error)
}

type SubscriptionDraftApproveService interface {
	Approve(context.Context, domain.ResolvedIdentity, service.SubscriptionCreateRequest) (*service.SubscriptionDraftApproveResult, error)
}

// SubscriptionRenewHandler serves subscription/renew and
// subscription/associates-draft/list/approve (@api_view(['POST']),
// IsAuthenticated); see subscription-renew.md for the refused paths.
type SubscriptionRenewHandler struct {
	renewals   SubscriptionRenewService
	approvals  SubscriptionDraftApproveService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewSubscriptionRenewHandler(renewals SubscriptionRenewService, approvals SubscriptionDraftApproveService, actors *service.ActorAuthentication,
	identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *SubscriptionRenewHandler {
	return &SubscriptionRenewHandler{renewals: renewals, approvals: approvals, actors: actors, identities: identities, audit: audit,
		operations: operations}
}

// serve runs the shared prologue and calls run with the admitted request.
func (h *SubscriptionRenewHandler) serve(c echo.Context, operation, name string, run func(domain.ResolvedIdentity, service.SubscriptionCreateRequest) error) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, name, "")
	}
	if request.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	input := service.SubscriptionCreateRequest{Body: write.Body, Form: write.Form, Group: request.Header.Get("X-Group-Id"), Audit: write.Audit}
	if values, present := request.Header[http.CanonicalHeaderKey("Authorization")]; present && len(values) > 0 {
		value := values[0]
		input.Authorization = &value
	}
	c.SetRequest(request.WithContext(write.Context))
	return run(*identity, input)
}

// createFailure renders create_subscription's APIExceptions as subscription/add does.
func createFailure(c echo.Context, err error) error {
	var invalid *domain.SubscriptionCreateInvalid
	var denied *domain.PermissionDenied
	request := c.Request()
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
	case errors.Is(err, domain.ErrSubscriptionCreateDuplicate):
		return c.JSON(409, map[string]string{"msg": "Questa iscrizione esiste già.", "code": "409", "ref": "subscription_duplicate"})
	case errors.As(err, &denied):
		return restError(c, denied)
	}
	return messageReadsError(c, err)
}

func (h *SubscriptionRenewHandler) Renew(c echo.Context) error {
	return h.serve(c, "subscription_renew", "Subscription Renew", func(identity domain.ResolvedIdentity, input service.SubscriptionCreateRequest) error {
		result, err := h.renewals.Renew(c.Request().Context(), identity, input)
		var failure *service.SubscriptionRenewFailure
		if errors.As(err, &failure) {
			return c.JSON(400, map[string]string{"status": "error", "msg": failure.Text})
		}
		if err != nil {
			return createFailure(c, err)
		}
		payment := "null"
		if result.PaymentID != nil {
			payment = `"` + *result.PaymentID + `"`
		}
		var body strings.Builder
		body.WriteString(`{"status":"success","payment_id":`)
		body.WriteString(payment)
		body.WriteString(`}`)
		return c.JSONBlob(200, []byte(body.String()))
	})
}

func (h *SubscriptionRenewHandler) Approve(c echo.Context) error {
	return h.serve(c, "subscription_associates_draft_approve", "Subscription Associates Draft Approve",
		func(identity domain.ResolvedIdentity, input service.SubscriptionCreateRequest) error {
			result, err := h.approvals.Approve(c.Request().Context(), identity, input)
			switch {
			case errors.Is(err, domain.ErrAssociateDraftUnauthorized):
				return c.NoContent(http.StatusUnauthorized) // Response(status=401): no challenge
			case errors.Is(err, service.ErrDraftApproveMissingIDs):
				return c.JSON(400, map[string]string{"msg": "bad request, missing drafts to approve."})
			case errors.Is(err, service.ErrDraftApproveNoneValid):
				return c.JSON(400, map[string]string{"msg": "Dati mancanti, iscrizione non approvata."})
			case err != nil:
				return messageReadsError(c, err)
			}
			return c.JSON(200, map[string]any{"msg": fmt.Sprintf("%d su %d atleti approvati.", result.Approved, result.Total),
				"approved": result.Approved})
		})
}
