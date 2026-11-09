package handler

import (
	"context"
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

type EmailRenderService interface {
	AddCollaborator(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext, language string) error
	RequestPayment(ctx context.Context, identity domain.ResolvedIdentity, uid, group, language string) error
	SendInvoice(ctx context.Context, identity domain.ResolvedIdentity, uid, group, language string) error
}

// EmailRenderHandler serves collaborators/add, payment/<uid>/request and
// invoice/<uid>/send (@api_view(['POST'])): each renders its e-mail like
// render_to_string (internal/emailrender) in the language LocaleMiddleware
// activates and publishes core.tasks.send_mail_async.
type EmailRenderHandler struct {
	emails     EmailRenderService
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewEmailRenderHandler(emails EmailRenderService, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *EmailRenderHandler {
	return &EmailRenderHandler{emails: emails, actors: actors, identities: identities, audit: audit, operations: operations}
}

const emailRenderDescription = "API endpoint to add a new payment"

func emailRenderError(c echo.Context, err error) error {
	var input *service.EmailInputError
	var malformed *service.PaymentInfoUUIDError
	switch {
	case err == nil:
		return c.JSON(http.StatusOK, map[string]string{"msg": "email sent."})
	case errors.As(err, &input):
		return c.JSON(400, []string{input.Detail})
	case errors.As(err, &malformed):
		return c.JSON(400, []string{malformed.Detail})
	case errors.Is(err, service.ErrEmailNotAllowed):
		return restError(c, &domain.PermissionDenied{Detail: err.Error()})
	case errors.Is(err, service.ErrEmailAlreadyPaid), errors.Is(err, service.ErrEmailNoRecipient):
		return c.JSON(http.StatusPreconditionFailed, map[string]string{"msg": err.Error()})
	}
	return messageReadsError(c, err)
}

// Add follows collaborators_add (IsAuthenticated, IsAssociationUser).
func (h *EmailRenderHandler) Add(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, "collaborators_add")
	if identity == nil {
		return err
	}
	request := c.Request()
	if role := identity.ScopeUser.Role; role != domain.RoleAssociation && role != domain.RoleCollaborator {
		return restError(c, &domain.PermissionDenied{Detail: messageReadsPermissionDetail(request)})
	}
	if done, err := dispatchMedical(c, http.MethodPost, "Collaborators Add"); done {
		return err
	}
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	err = h.emails.AddCollaborator(write.Context, *identity, write.Body, write.Audit, djangoReadRouteLocale{}.Language(request))
	if err == nil {
		return c.JSON(http.StatusOK, map[string]string{"msg": "User invited"})
	}
	return emailRenderError(c, err)
}

// Request follows payment_request; the view never reads request.data.
func (h *EmailRenderHandler) Request(c echo.Context) error {
	return h.send(c, "payment_request", "Payment Request", h.emails.RequestPayment)
}

// Send follows invoice_send; the view never reads request.data.
func (h *EmailRenderHandler) Send(c echo.Context) error {
	return h.send(c, "invoice_send", "Invoice Send", h.emails.SendInvoice)
}

func (h *EmailRenderHandler) send(c echo.Context, operation, name string,
	run func(context.Context, domain.ResolvedIdentity, string, string, string) error) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPost, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, name, emailRenderDescription)
	}
	if request.Method != http.MethodPost {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	tracked, err := h.operations.Track(request.Context())
	if err != nil {
		return paymentCategoryError(c, err)
	}
	defer tracked.Release()
	ctx, err := tracked.Admit()
	if err != nil {
		return paymentCategoryError(c, err)
	}
	return emailRenderError(c, run(ctx, *identity, c.Param("uid"), request.Header.Get("X-Group-Id"), djangoReadRouteLocale{}.Language(request)))
}
