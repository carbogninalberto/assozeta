package handler

import (
	"errors"
	"net/http"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// SubscriptionImportUploadHandler serves subscription_import_upload (POST).
type SubscriptionImportUploadHandler struct {
	imports *service.SubscriptionImportUpload
	uploads *DocumentUploadsHandler
}

func NewSubscriptionImportUploadHandler(imports *service.SubscriptionImportUpload, actors *service.ActorAuthentication,
	identities *service.IdentityResolver, audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *SubscriptionImportUploadHandler {
	return &SubscriptionImportUploadHandler{imports: imports, uploads: &DocumentUploadsHandler{actors: actors, identities: identities, audit: audit,
		operations: operations}}
}

func (h *SubscriptionImportUploadHandler) Upload(c echo.Context) error {
	raw, err := bufferMultipart(c)
	if err != nil {
		return restError(c, err)
	}
	identity, err := messageMutationsIdentity(c, h.uploads.actors, h.uploads.identities, http.MethodPost, "subscription_import_upload")
	if identity == nil {
		return err
	}
	if done, err := dispatchMedical(c, http.MethodPost, "Subscription Import Upload"); done {
		return err
	}
	ctx, body, audit, release, err := h.uploads.admit(c, uploadBody(c, raw))
	if err != nil {
		var input *restInputError
		if errors.As(err, &input) {
			return restError(c, err)
		}
		return err
	}
	defer release()
	result, err := h.imports.Upload(ctx, *identity, body, audit, c.Request().ContentLength > domain.RequestUploadMemory)
	var large *service.SubscriptionImportTooLarge
	switch {
	case err == nil:
		return c.Blob(http.StatusOK, echo.MIMEApplicationJSON, dto.PythonJSONDumpsCompact(result))
	case errors.As(err, &large):
		return c.JSON(http.StatusRequestEntityTooLarge, map[string]string{"msg": large.Message()})
	case errors.Is(err, service.ErrSubscriptionImportFailed):
		return c.JSON(http.StatusBadRequest, map[string]string{"msg": service.SubscriptionImportFailedMessage})
	case errors.Is(err, service.ErrSubscriptionImportUnauthorized):
		return c.NoContent(http.StatusUnauthorized)
	case errors.Is(err, service.ErrSubscriptionImportNotPorted):
		c.Logger().Error(err)
	}
	return h.uploads.failure(c, err)
}
