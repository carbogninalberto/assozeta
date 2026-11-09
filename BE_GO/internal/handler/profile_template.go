package handler

import (
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// ProfileTemplateHandler serves profile/update/subscription/template.
type ProfileTemplateHandler struct {
	template   *service.ProfileTemplate
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewProfileTemplateHandler(template *service.ProfileTemplate, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *ProfileTemplateHandler {
	return &ProfileTemplateHandler{template: template, actors: actors, identities: identities, audit: audit, operations: operations}
}

// Update mirrors profile_update_subscription_template (@api_view(['PATCH'])).
func (h *ProfileTemplateHandler) Update(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPatch, "profile_update_subscription_template")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Profile Update Subscription Template", "")
	}
	if request.Method != http.MethodPatch {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	response, err := h.template.Update(write.Context, *identity, write.Body, write.Audit)
	if err != nil {
		return messageReadsError(c, err)
	}
	return c.JSON(200, response)
}
