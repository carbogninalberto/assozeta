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

// OAuthAccountsHandler serves oauth2/signup, oauth2/partial-signup,
// oauth2/reset (AllowAny: a supplied token still authenticates) and
// oauth2/delete-account (IsAuthenticated), all @api_view(['POST']).
type OAuthAccountsHandler struct {
	accounts   *service.OAuthAccounts
	actors     *service.ActorAuthentication
	identities *service.IdentityResolver
	audit      LoginAuditReader
	operations *paymentcategorylifecycle.Lifecycle
}

func NewOAuthAccountsHandler(accounts *service.OAuthAccounts, actors *service.ActorAuthentication, identities *service.IdentityResolver,
	audit LoginAuditReader, operations *paymentcategorylifecycle.Lifecycle) *OAuthAccountsHandler {
	return &OAuthAccountsHandler{accounts: accounts, actors: actors, identities: identities, audit: audit, operations: operations}
}

const oauthSignupDescription = "API endpoint to sign up a user by oauth2 cap\n:param request: data: {first_name, last_name, username, email, " +
	"password} or {token, user}\n:return: token, refresh token"

// admit runs identity resolution, OPTIONS, method dispatch and the write
// prologue; a nil write means the response has been written.
func (h *OAuthAccountsHandler) admit(c echo.Context, operation, name, description string, anonymous bool) (*domain.ResolvedIdentity, *restWrite, func(), error) {
	identity, err := messageMutationsIdentityFor(c, h.actors, h.identities, http.MethodPost, operation, anonymous)
	if identity == nil {
		return nil, nil, nil, err
	}
	request := c.Request()
	if request.Method == http.MethodOptions {
		return nil, nil, nil, supplierOptions(c, name, description)
	}
	if request.Method != http.MethodPost {
		return nil, nil, nil, restError(c, &restInputError{Status: 405, Detail: restText(request, "method")})
	}
	write, release, err := admitRESTWrite(c, h.operations, h.audit)
	if write == nil {
		return nil, nil, nil, err
	}
	return identity, write, release, nil
}

func oauthAnswer(c echo.Context, result any, err error) error {
	var invalid *service.OAuthSignupInvalid
	switch {
	case errors.As(err, &invalid):
		return c.JSON(400, map[string]any{"msg": "Invalid request.", "details": oauthSignupMessages(c.Request(), invalid.Errors)})
	case err != nil:
		return messageReadsError(c, err)
	}
	if answer, ok := result.(service.OAuthAnswer); ok {
		return c.JSON(answer.Status, answer.Body)
	}
	return c.JSON(http.StatusOK, result)
}

// oauthSignupMessages renders UserSerializerSignup's error codes.
func oauthSignupMessages(request *http.Request, errors map[string][]string) map[string][]string {
	english := restLanguage(request) == "en"
	result := make(map[string][]string, len(errors))
	for field, codes := range errors {
		messages := make([]string, 0, len(codes))
		for _, code := range codes {
			switch {
			case code == dto.OAuthUsernameInvalid && english:
				messages = append(messages, "Enter a valid username. This value may contain only letters, numbers, and @/./+/-/_ characters.")
			case code == dto.OAuthUsernameInvalid:
				messages = append(messages, "Immetti un nome utente valido. Questo campo può contenere solo lettere dell'alfabeto inglese, numeri e caratteri @/./+/-/_ .")
			case code == dto.OAuthUsernameUnique && english:
				messages = append(messages, "A user with that username already exists.")
			case code == dto.OAuthUsernameUnique:
				messages = append(messages, "Un utente con questo nome è già presente.")
			case code == dto.OAuthPasswordPattern && english:
				messages = append(messages, "This value does not match the required pattern.")
			case code == dto.OAuthPasswordPattern:
				messages = append(messages, "Questo valore non corrisponde alla sequenza richiesta.")
			default:
				messages = append(messages, paymentCategoryValidationMessage(code, english))
			}
		}
		result[field] = messages
	}
	return result
}

// Signup follows oauth2_signup.
func (h *OAuthAccountsHandler) Signup(c echo.Context) error {
	identity, write, release, err := h.admit(c, "oauth2_signup", "Oauth2 Signup", oauthSignupDescription, true)
	if write == nil {
		return err
	}
	defer release()
	result, err := h.accounts.Signup(write.Context, *identity, write.Body, write.Audit, djangoReadRouteLocale{}.Language(c.Request()))
	return oauthAnswer(c, result, err)
}

// PartialSignup follows partial_signup.
func (h *OAuthAccountsHandler) PartialSignup(c echo.Context) error {
	identity, write, release, err := h.admit(c, "partial_signup", "Partial Signup", "", true)
	if write == nil {
		return err
	}
	defer release()
	if err = h.accounts.PartialSignup(write.Context, *identity, write.Body, write.Audit); err != nil {
		return messageReadsError(c, err)
	}
	return c.JSON(http.StatusOK, map[string]string{"msg": "Email added."})
}

// Reset follows oauth2_reset_password.
func (h *OAuthAccountsHandler) Reset(c echo.Context) error {
	_, write, release, err := h.admit(c, "oauth2_reset_password", "Oauth2 Reset Password",
		"API endpoint to reset password of a user.\n:param request:\n:return:", true)
	if write == nil {
		return err
	}
	defer release()
	answer, err := h.accounts.Reset(write.Context, write.Body, djangoReadRouteLocale{}.Language(c.Request()))
	return oauthAnswer(c, answer, err)
}

// DeleteAccount follows oauth2_delete_account (IsAuthenticated).
func (h *OAuthAccountsHandler) DeleteAccount(c echo.Context) error {
	identity, write, release, err := h.admit(c, "oauth2_delete_account", "Oauth2 Delete Account", "", false)
	if write == nil {
		return err
	}
	defer release()
	if err = h.accounts.DeleteAccount(write.Context, *identity, write.Audit); err != nil {
		return messageReadsError(c, err)
	}
	return c.JSON(http.StatusOK, map[string]string{"msg": "Account will be deleted in 30 days."})
}
