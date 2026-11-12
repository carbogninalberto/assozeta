package handler

import (
	"context"
	"errors"
	"net/http"
	"net/url"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/paymentcategorylifecycle"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// ProfileMiscHandler serves profile/associates/course/<uid>,
// profile/associates/sport-association/<uid>, profile/image/<uid> and
// onboarding/update.
type ProfileMiscHandler struct {
	misc       *service.ProfileMisc
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewProfileMiscHandler(misc *service.ProfileMisc, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *ProfileMiscHandler {
	return &ProfileMiscHandler{misc: misc, actors: actors, identities: identities, audit: audit, operations: operations}
}

func profileMiscUID(c echo.Context) (string, error) {
	identifier := c.Param("uid")
	if c.Request().URL.RawPath != "" {
		return url.PathUnescape(identifier)
	}
	return identifier, nil
}

func profileMiscFailure(c echo.Context, err error) error {
	var invalid *service.PaymentInfoUUIDError
	if errors.As(err, &invalid) {
		return c.JSON(400, []string{invalid.Detail})
	}
	return messageReadsError(c, err)
}

const (
	profileAssociatesCourseDescription = "Select all the associates that are subscribed on the course and are connected to the user,\n" +
		"plus return a list of possible subscriptions"
	profileAssociatesSportDescription = "Select all the associates that are subscribed on a sport association and are connected to the user,\n" +
		"plus return a list of possible subscriptions"
)

// Course mirrors profile_associates_course (@api_view(['GET'])).
func (h *ProfileMiscHandler) Course(c echo.Context) error {
	return h.associates(c, "profile_associates_course", "Profile Associates Course", profileAssociatesCourseDescription, h.misc.CourseAssociates)
}

// SportAssociation mirrors profile_associates_sport_association (@api_view(['GET'])).
func (h *ProfileMiscHandler) SportAssociation(c echo.Context) error {
	return h.associates(c, "profile_associates_sport_association", "Profile Associates Sport Association", profileAssociatesSportDescription,
		h.misc.SportAssociates)
}

func (h *ProfileMiscHandler) associates(c echo.Context, operation, name, description string,
	read func(ctx context.Context, identity domain.ResolvedIdentity, uid, group string) (map[string]any, error)) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodGet, operation)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, name, description)
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	uid, err := profileMiscUID(c)
	if err != nil {
		return restError(c, err)
	}
	body, err := read(request.Context(), *identity, uid, request.Header.Get("X-Group-Id"))
	if err != nil {
		return profileMiscFailure(c, err)
	}
	return c.JSON(200, body)
}

// Image mirrors profile_image (@api_view(['GET']), AllowAny).
func (h *ProfileMiscHandler) Image(c echo.Context) error {
	identity, err := messageMutationsIdentityFor(c, h.actors, h.identities, http.MethodGet, "profile_image", true)
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Profile Image", "")
	}
	if request.Method != http.MethodGet {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	uid, err := profileMiscUID(c)
	if err != nil {
		return restError(c, err)
	}
	avatar, err := h.misc.Image(request.Context(), uid)
	if err != nil {
		return profileMiscFailure(c, err)
	}
	if avatar == nil {
		// Response(None): the JSON renderer writes no body.
		return c.Blob(200, "application/json", nil)
	}
	return c.JSON(200, *avatar)
}

// Onboarding mirrors onboarding_update (@api_view(['PATCH'])).
func (h *ProfileMiscHandler) Onboarding(c echo.Context) error {
	identity, err := messageMutationsIdentity(c, h.actors, h.identities, http.MethodPatch, "onboarding_update")
	if identity == nil {
		return err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return supplierOptions(c, "Onboarding Update", "")
	}
	if request.Method != http.MethodPatch {
		return restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return err
	}
	defer release()
	// Django keeps a file part in memory (InMemoryUploadedFile) while the body is at most
	// FILE_UPLOAD_MAX_MEMORY_SIZE; larger bodies use another file class, not reproduced.
	if (request.ContentLength < 0 || request.ContentLength > 2621440) && dto.ProfileSettingsHasUpload(write.Body) {
		return messageReadsError(c, service.ErrOnboardingNotPorted)
	}
	err = h.misc.Onboarding(write.Context, *identity, write.Body, write.Audit)
	var invalid *service.OnboardingValidationError
	var fresh *service.OnboardingFreshError
	var server *service.OnboardingServerError
	switch {
	case err == nil:
		return c.JSON(200, map[string]string{"message": "Updated successfully"})
	case errors.Is(err, service.ErrOnboardingNotAssociation):
		return c.JSON(403, map[string]string{"error": err.Error()})
	case errors.As(err, &invalid):
		english := restLanguage(request) == "en"
		message := func(code string) string { return paymentCategoryValidationMessage(code, english) }
		return c.JSON(400, map[string]string{"error": dto.DRFErrorRepr(invalid.Fields, message)})
	case errors.As(err, &fresh):
		return c.JSON(400, map[string]any{"error": "Fresh self-host onboarding requires a complete membership payload.", "details": fresh.Details})
	case errors.As(err, &server):
		c.Logger().Errorf("onboarding update error: %s", server.Message)
		return c.JSON(500, map[string]string{"error": service.OnboardingUpdateErrorMessage})
	}
	return messageReadsError(c, err)
}
