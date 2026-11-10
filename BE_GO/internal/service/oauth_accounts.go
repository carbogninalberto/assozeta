package service

import (
	"bytes"
	"context"
	"crypto/rand"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/emailrender"
	"github.com/google/uuid"
)

var (
	// ErrOAuthServer stands for the views' uncaught exceptions (the 500 page).
	ErrOAuthServer = errors.New("oauth2 account view raised")
	// ErrOAuthNotPorted refuses inputs outside the port (500 before any write).
	ErrOAuthNotPorted = errors.New("oauth2 account input not ported")
)

// OAuthAnswer is a Response(...) the views build themselves.
type OAuthAnswer struct {
	Status int
	Body   any
}

// OAuthSignupInvalid is {'msg': 'Invalid request.', 'details': serializer.errors}
// as validation codes; the transport renders them in the request's language.
type OAuthSignupInvalid struct{ Errors map[string][]string }

func (e *OAuthSignupInvalid) Error() string { return "Invalid request." }

// OAuthAccountSettings are the core.settings values the views read.
type OAuthAccountSettings struct {
	AppURL, WhitelabelName, DefaultFromEmail string
	IsWhitelabel, Debug                      bool
}

type OAuthPasswordEncoder interface {
	Encode(context.Context, string) (string, error)
}

type OAuthAccounts struct {
	store        domain.OAuthAccountsStore
	resetTokens  domain.OAuthResetTokens
	publisher    celery.Publisher
	hashes       OAuthPasswordEncoder
	tokens       LoginTokenIssuer
	responses    LoginResponseBuilder
	associations LoginResponseAssociations
	social       SocialTokenVerifier
	candidates   SocialLoginCandidates
	secondFactor LoginSecondFactor
	settings     OAuthAccountSettings
	now          func() time.Time
	random       func([]byte) (int, error)
}

func NewOAuthAccounts(store domain.OAuthAccountsStore, resetTokens domain.OAuthResetTokens, publisher celery.Publisher,
	hashes OAuthPasswordEncoder, tokens LoginTokenIssuer, responses LoginResponseBuilder, associations LoginResponseAssociations,
	social SocialTokenVerifier, candidates SocialLoginCandidates, secondFactor LoginSecondFactor, settings OAuthAccountSettings,
	now func() time.Time) *OAuthAccounts {
	return &OAuthAccounts{store: store, resetTokens: resetTokens, publisher: publisher, hashes: hashes, tokens: tokens,
		responses: responses, associations: associations, social: social, candidates: candidates, secondFactor: secondFactor,
		settings: settings, now: now, random: rand.Read}
}

var oauthInvalid = map[string]string{"msg": "Invalid request."}

// oauthAudit is the request's audit context: the authenticated account, or
// none for an anonymous request (AllowAny views).
func oauthAudit(identity domain.ResolvedIdentity, audit domain.LoginAuditContext) domain.LoginAuditContext {
	if identity.Actor == nil {
		audit.ActorID, audit.ActorEmail, audit.AdditionalData = nil, nil, nil
		return audit
	}
	return paymentCategoryAuditContext(identity, audit)
}

// oauthObject is request.data for views that call .get()/.keys() on it: an
// empty body is an empty QueryDict; any other non-dict body raises.
func oauthObject(body any) (domain.RequestObject, error) {
	if body == nil {
		return domain.RequestObject{}, nil
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		return nil, ErrOAuthServer
	}
	return object, nil
}

func (s *OAuthAccounts) render(ctx context.Context, name string, context map[string]any, language string) (string, error) {
	stored, err := s.store.InstanceConfig(ctx)
	if err != nil {
		return "", err
	}
	var config *emailrender.InstanceConfig
	if stored.Found {
		config = &emailrender.InstanceConfig{SelfHosted: stored.SelfHosted, PrimaryColor: stored.PrimaryColor, Name: stored.Name,
			LogoPath: stored.LogoPath, UpdatedAt: stored.UpdatedAt}
	}
	brand, err := emailrender.Brand(config, emailrender.Settings{AppURL: s.settings.AppURL, WhitelabelName: s.settings.WhitelabelName,
		IsWhitelabel: s.settings.IsWhitelabel})
	if err != nil {
		return "", err
	}
	return emailrender.Render(name, context, emailrender.Options{Language: language, Brand: brand})
}

func (s *OAuthAccounts) brandContext(values map[string]any) map[string]any {
	values["app_host"] = s.settings.AppURL
	values["settings"] = map[string]any{"WHITELABEL_NAME": s.settings.WhitelabelName, "IS_WHITELABEL": s.settings.IsWhitelabel}
	return values
}

func (s *OAuthAccounts) publish(ctx context.Context, task celery.Task) error {
	publish, cancel := context.WithTimeout(context.WithoutCancel(ctx), subscriptionUpdatePublishTimeout)
	defer cancel()
	_, err := s.publisher.Publish(publish, task)
	return err
}

// sendMail is AuthUtils' send_mail_async.apply_async(kwargs={...}).
func (s *OAuthAccounts) sendMail(ctx context.Context, subject, message, recipient string) error {
	return s.publish(ctx, celery.Task{Name: sendMailTask, Kwargs: celery.Dict{
		{Key: "subject", Value: subject}, {Key: "message", Value: message}, {Key: "from_email", Value: s.settings.DefaultFromEmail},
		{Key: "recipient_list", Value: []any{recipient}}, {Key: "html_message", Value: message}, {Key: "fail_silently", Value: false},
	}})
}

// sendText is send_email_text.delay(recipient_list=..., subject=..., message=...).
func (s *OAuthAccounts) sendText(ctx context.Context, recipient, subject, message string) error {
	return s.publish(ctx, celery.Task{Name: "send_email_text", Kwargs: celery.Dict{
		{Key: "recipient_list", Value: []any{recipient}}, {Key: "subject", Value: subject}, {Key: "message", Value: message},
	}})
}

func oauthTextUnsupported(text string) bool {
	return strings.ContainsRune(text, 0) || !utf8.ValidString(text) || wtf8Surrogate(text)
}

// --- oauth2/partial-signup ----------------------------------------------------------------------

// PartialSignup follows partial_signup.
func (s *OAuthAccounts) PartialSignup(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) error {
	object, err := oauthObject(body)
	if err != nil {
		return err
	}
	value, present := object.Lookup("email")
	if !present {
		return nil
	}
	// email__iexact passes the value unprepared: anything but a string
	// raises in PostgreSQL (None reaches create() and its NOT NULL column).
	email, ok := value.(string)
	if !ok || oauthTextUnsupported(email) {
		return ErrOAuthServer
	}
	exists, err := s.store.PartialExists(ctx, email)
	if err != nil || exists {
		return err
	}
	if utf8.RuneCountInString(email) > 254 { // varchar(254): the INSERT raises
		return ErrOAuthServer
	}
	if err = s.store.CreatePartial(ctx, email, s.now().UTC(), oauthAudit(identity, audit)); err != nil {
		return err
	}
	return s.sendText(ctx, "info@bakney.com", "Nuovo utente parziale", "Utente parziale con email "+email+" registrato.")
}

// --- oauth2/delete-account ----------------------------------------------------------------------

// DeleteAccount follows oauth2_delete_account: the scope user, or the acting
// collaborator, gets delete_on = UTC today + 30 days.
func (s *OAuthAccounts) DeleteAccount(ctx context.Context, identity domain.ResolvedIdentity, audit domain.LoginAuditContext) error {
	user := identity.ScopeUser
	if identity.Collaborator {
		user = identity.Effective
	}
	if user == nil {
		return ErrOAuthServer
	}
	now := s.now().UTC()
	date := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC).AddDate(0, 0, 30)
	deletion, err := s.store.ScheduleDeletion(ctx, user.ID, date, now, oauthAudit(identity, audit))
	if err != nil {
		return err
	}
	return s.sendText(ctx, deletion.Email, s.settings.WhitelabelName+" | Eliminazione account",
		"La tua richiesta di eliminazione dell'account è stata presa in carico correttamente. "+
			"L'account verrà eliminato il giorno "+deletion.DeleteOn.Format("02/01/2006")+".")
}

// --- oauth2/reset -------------------------------------------------------------------------------

// redisKey is redis-py's key encoding: strings as UTF-8 (lone surrogates
// raise), ints by str(), floats by repr(); other types raise DataError.
func redisKey(value any) (string, bool) {
	switch v := value.(type) {
	case string:
		return v, utf8.ValidString(v) && !wtf8Surrogate(v)
	case json.Number:
		return dto.PythonStr(v), true
	}
	return "", false
}

// Reset follows oauth2_reset_password.
func (s *OAuthAccounts) Reset(ctx context.Context, body any, language string) (OAuthAnswer, error) {
	object, err := oauthObject(body)
	if err != nil {
		return OAuthAnswer{}, err
	}
	if value, present := object.Lookup("email"); present && len(object) == 1 {
		users, err := s.store.ResetUsers(ctx, dto.PythonStrip(dto.PythonStr(value)))
		if err != nil {
			return OAuthAnswer{}, err
		}
		for _, user := range users {
			var raw [16]byte
			if _, err = s.random(raw[:]); err != nil {
				return OAuthAnswer{}, err
			}
			token := hex.EncodeToString(raw[:])
			if err = s.resetTokens.Set(ctx, token, user.ID, time.Hour); err != nil {
				return OAuthAnswer{}, err
			}
			message, err := s.render(ctx, emailrender.AccountReset, s.brandContext(map[string]any{"first_name": user.FirstName,
				"last_name": user.LastName, "username": user.Username, "token": token}), language)
			if err != nil {
				return OAuthAnswer{}, err
			}
			if err = s.sendMail(ctx, s.settings.WhitelabelName+" | Reset Password", message, user.Email); err != nil {
				return OAuthAnswer{}, err
			}
		}
		return OAuthAnswer{200, map[string]string{"msg": "Email sent to the user if the email exists."}}, nil
	}
	tokenValue, hasToken := object.Lookup("token")
	passwordValue, hasPassword := object.Lookup("password")
	if !hasToken || !hasPassword || len(object) != 2 {
		return OAuthAnswer{400, oauthInvalid}, nil
	}
	key, ok := redisKey(tokenValue)
	if !ok {
		return OAuthAnswer{}, ErrOAuthServer
	}
	stored, err := s.resetTokens.Get(ctx, key)
	if err != nil {
		return OAuthAnswer{}, err
	}
	password, ok := passwordValue.(string) // re.search on a non-string raises
	if !ok {
		return OAuthAnswer{}, ErrOAuthServer
	}
	if stored == nil || !dto.OAuthPasswordMatches(password) {
		return OAuthAnswer{400, oauthInvalid}, nil
	}
	if oauthTextUnsupported(password) {
		return OAuthAnswer{}, ErrOAuthNotPorted
	}
	id, err := paymentInfoParseUUID(*stored)
	if err != nil {
		return OAuthAnswer{}, ErrOAuthServer // UUIDField.to_python: ValidationError
	}
	user, err := s.store.ResetUser(ctx, id.String())
	if err != nil {
		return OAuthAnswer{}, err
	}
	if user == nil {
		return OAuthAnswer{}, ErrOAuthServer // User.DoesNotExist
	}
	hash, err := s.hashes.Encode(ctx, password)
	if err != nil {
		return OAuthAnswer{}, err
	}
	if err = s.store.SetPassword(ctx, user.ID, hash); err != nil {
		return OAuthAnswer{}, err
	}
	if err = s.resetTokens.Delete(ctx, key); err != nil {
		return OAuthAnswer{}, err
	}
	message, err := s.render(ctx, emailrender.AccountResetConfirm, s.brandContext(map[string]any{"first_name": user.FirstName,
		"last_name": user.LastName}), language)
	if err != nil {
		return OAuthAnswer{}, err
	}
	if err = s.sendMail(ctx, s.settings.WhitelabelName+" | Conferma Nuova Password", message, user.Email); err != nil {
		return OAuthAnswer{}, err
	}
	return OAuthAnswer{200, map[string]string{"msg": "Password reset."}}, nil
}

// --- oauth2/signup ------------------------------------------------------------------------------

//go:embed oauth_disposable_domains.txt
var oauthDisposableList string

// oauthDisposable is DisposableEmailChecker's local list check
// (DEC_LOADER = application.disposable_email.custom_email_domain_loader):
// re.match('.*<domain>$') per entry, with the entries as regular expressions.
var oauthDisposable = func() *regexp.Regexp {
	entries := strings.Fields(oauthDisposableList)
	parts := make([]string, len(entries))
	for i, entry := range entries {
		parts[i] = "(?:.*" + entry + ")"
	}
	return regexp.MustCompile(`^(?:` + strings.Join(parts, "|") + `)$`)
}()

func oauthSignupAnswer(status int, field, message string) OAuthAnswer {
	return OAuthAnswer{status, map[string]any{"msg": "Invalid request.", "details": map[string][]string{field: {message}}}}
}

// pythonFalsy is `not value` for parsed JSON values.
func pythonFalsy(value any) bool {
	switch v := value.(type) {
	case nil:
		return true
	case bool:
		return !v
	case string:
		return v == ""
	case json.Number:
		number, err := strconv.ParseFloat(string(v), 64)
		return err == nil && number == 0
	case []any:
		return len(v) == 0
	case domain.RequestObject:
		return len(v) == 0
	}
	return false
}

// transferTokenValid is SubscriptionTransfer.objects.filter(token=value):
// UUIDField.to_python raises for values uuid.UUID rejects.
func transferTokenValid(value any) bool {
	switch v := value.(type) {
	case nil:
		return true
	case bool:
		return true // UUID(int=True)
	case string:
		_, err := paymentInfoParseUUID(v)
		return err == nil
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			return false // float: hex.replace raises
		}
		number, ok := new(big.Int).SetString(string(v), 10)
		return ok && number.Sign() >= 0 && number.BitLen() <= 128
	}
	return false
}

// normalizeEmail is BaseUserManager.normalize_email.
func normalizeEmail(email string) string {
	at := strings.LastIndex(email, "@")
	if at < 0 {
		return email
	}
	return email[:at] + "@" + strings.ToLower(email[at+1:])
}

// Signup follows oauth2_signup. The result is the response body for 200, or
// an OAuthAnswer.
func (s *OAuthAccounts) Signup(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext,
	language string) (any, error) {
	object, err := oauthObject(body)
	if err != nil {
		return nil, err
	}
	association, present := object.Lookup("sport_association")
	if !present {
		return oauthSignupAnswer(400, "sport_association", "This field is required."), nil
	}
	if flag, isBool := association.(bool); !isBool {
		return oauthSignupAnswer(400, "sport_association", "Must be a boolean."), nil
	} else if flag {
		return oauthSignupAnswer(403, "sport_association", "Association accounts can only be created during instance setup."), nil
	}
	emailValue, present := object.Lookup("email")
	if !present {
		return nil, ErrOAuthServer
	}
	rawEmail, ok := emailValue.(string)
	if !ok {
		return nil, ErrOAuthServer // .lower() / .split()
	}
	if !dto.IsASCII(rawEmail) {
		return nil, ErrOAuthNotPorted // Python's Unicode lower()/upper()
	}
	if !s.settings.Debug {
		lower := strings.ToLower(rawEmail)
		if dto.SupplierEmail(lower) && oauthDisposable.MatchString(lower) {
			return OAuthAnswer{400, map[string]string{"msg": "You are nasty 🖕( •_• )🖕"}}, nil
		}
	}
	parts := strings.Split(rawEmail, "@")
	if len(parts) < 2 {
		return nil, ErrOAuthServer // IndexError
	}
	if strings.ToLower(parts[1]) == "skrank.com" {
		return nil, ErrOAuthServer // `raise ValidationError` without a message: TypeError
	}
	_, collaborator := object.Lookup("collaboratorToken")
	transfer := false
	if flag, present := object.Lookup("subscription_transfer"); present {
		if token, present := object.Lookup("subscription_transfer_token"); present {
			transfer = flag == true
			if !pythonFalsy(token) && !transferTokenValid(token) {
				return nil, ErrOAuthServer
			}
		}
	}
	_, hasToken := object.Lookup("token")
	_, hasUsername := object.Lookup("username")
	if backend, hasBackend := object.Lookup("backend"); hasToken && hasUsername && hasBackend {
		provider := ""
		switch backend {
		case "google-oauth2", "google-identity", "google":
			provider = "google"
		case "apple-id", "apple":
			provider = "apple"
		default:
			return OAuthAnswer{400, map[string]string{"error": "Unsupported backend: " + dto.PythonStr(backend)}}, nil
		}
		token, _ := object.Lookup("token")
		return s.socialSignup(ctx, identity, object, provider, token, rawEmail, collaborator, transfer, audit, language)
	}
	fields, validation, unported := dto.ParseOAuthSignup(object)
	if unported {
		return nil, ErrOAuthNotPorted
	}
	if fields.UsernameChecked {
		taken, err := s.store.UsernameTaken(ctx, fields.Username)
		if err != nil {
			return nil, err
		}
		var codes []string
		if validation.UsernameInvalid {
			codes = append(codes, dto.OAuthUsernameInvalid)
		}
		if taken {
			codes = append(codes, dto.OAuthUsernameUnique)
		}
		if codes = append(codes, validation.Errors["username"]...); len(codes) > 0 {
			validation.Errors["username"] = codes
		}
	}
	if len(validation.Errors) > 0 {
		return nil, &OAuthSignupInvalid{Errors: validation.Errors}
	}
	audit = oauthAudit(identity, audit)
	if err = s.store.DeletePartial(ctx, rawEmail, s.now().UTC(), audit); err != nil {
		return nil, err
	}
	membership, refusal, err := s.signupMembership(ctx, object, collaborator, audit)
	if err != nil || refusal != nil {
		return refusal, err
	}
	role, collaboratorRole, connected, permissions := membership.role, membership.collaboratorRole, membership.connected, membership.permissions
	if fields.FirstName == nil || fields.LastName == nil {
		return nil, ErrOAuthServer // validated_data[...]: KeyError
	}
	passwordValue, _ := object.Lookup("password")
	password, ok := passwordValue.(string)
	if !ok || oauthTextUnsupported(password) {
		return nil, ErrOAuthNotPorted
	}
	hash, err := s.hashes.Encode(ctx, password)
	if err != nil {
		return nil, err
	}
	user := domain.OAuthNewUser{ID: uuid.NewString(), Username: strings.ToUpper(fields.Username), Email: normalizeEmail(fields.Email),
		PasswordHash: hash, FirstName: *fields.FirstName, LastName: *fields.LastName, Role: role, CollaboratorRole: collaboratorRole,
		ConnectedUserID: connected, Permissions: permissions, Joined: s.now().UTC()}
	if err = s.store.CreateUser(ctx, user, audit); errors.Is(err, domain.ErrOAuthUsernameConflict) {
		return nil, ErrOAuthServer // IntegrityError
	} else if err != nil {
		return nil, err
	}
	if transfer {
		return nil, ErrOAuthServer // QuerySet.save: AttributeError
	}
	return s.welcomeNewUser(ctx, user, audit, language)
}

// oauthMembership is what create_user receives about the collaboration.
type oauthMembership struct {
	role, collaboratorRole int
	connected              *string
	permissions            json.RawMessage
}

// signupMembership is the role of a new account: an athlete, or a collaborator
// whose invite (CollaborationInvites .first() by token) is looked up, deleted
// and then checked for expiry. A non-nil answer refuses the signup.
func (s *OAuthAccounts) signupMembership(ctx context.Context, object domain.RequestObject, collaborator bool,
	audit domain.LoginAuditContext) (oauthMembership, any, error) {
	membership := oauthMembership{role: domain.RoleAthlete, collaboratorRole: 1}
	if !collaborator {
		return membership, nil, nil
	}
	var token *string
	if value, _ := object.Lookup("collaboratorToken"); value != nil {
		text := dto.PythonStr(value)
		if oauthTextUnsupported(text) {
			return membership, nil, ErrOAuthServer
		}
		token = &text
	}
	invite, err := s.store.Invite(ctx, token)
	if err != nil {
		return membership, nil, err
	}
	if invite == nil {
		return membership, OAuthAnswer{400, map[string]string{"msg": "Collaboration invite not found."}}, nil
	}
	if err = s.store.DeleteInvite(ctx, invite.ID, s.now().UTC(), audit); err != nil {
		return membership, nil, err
	}
	if invite.ExpirationDate == nil {
		return membership, nil, ErrOAuthServer // None < datetime: TypeError
	}
	if invite.ExpirationDate.Before(s.now()) {
		return membership, OAuthAnswer{400, map[string]string{"msg": "Collaboration invite expired."}}, nil
	}
	membership.role, membership.collaboratorRole, membership.connected = domain.RoleCollaborator, invite.Role, &invite.UserID
	if invite.Permissions != nil && *invite.Permissions != "null" {
		membership.permissions = json.RawMessage(*invite.Permissions)
	}
	return membership, nil, nil
}

// oauthUnusablePassword is make_password(None): '!' and 40 random letters and digits.
func (s *OAuthAccounts) oauthUnusablePassword() (string, error) {
	const alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
	value := []byte("!")
	for len(value) < 41 {
		index, err := rand.Int(rand.Reader, big.NewInt(int64(len(alphabet))))
		if err != nil {
			return "", err
		}
		value = append(value, alphabet[index.Int64()])
	}
	return string(value), nil
}

// socialSignup is oauth2_signup's token + username + backend path: the
// provider verifies the token and must state a verified e-mail. An account
// with that e-mail signs in under the login rules and is not modified;
// otherwise the account is created from the identity.
func (s *OAuthAccounts) socialSignup(ctx context.Context, identity domain.ResolvedIdentity, object domain.RequestObject, provider string,
	token any, rawEmail string, collaborator, transfer bool, audit domain.LoginAuditContext, language string) (any, error) {
	invalid := OAuthAnswer{401, map[string]string{"error": "Invalid social token"}}
	social, valid, err := s.social.Verify(ctx, provider, token)
	if err != nil {
		return nil, err
	}
	if !valid {
		return invalid, nil
	}
	email, verified := social.VerifiedEmail()
	if !verified {
		return invalid, nil
	}
	audit = oauthAudit(identity, audit)
	var existing domain.Account
	count := 0
	err = s.candidates.VisitLoginCandidates(ctx, true, email, func(account domain.Account) error {
		if count++; count == 1 {
			existing = account
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	if count > 0 {
		return s.existingAccountSignin(ctx, existing, count, object, audit)
	}
	// Values Python would convert differently are refused before any write.
	names := [2]string{}
	for index, pair := range [2][2]any{{social.FirstName, "first_name"}, {social.LastName, "last_name"}} {
		value := pair[0]
		if pythonFalsy(value) {
			value = ""
			if sent, present := object.Lookup(pair[1].(string)); present {
				value = sent
			}
		}
		text, ok := value.(string)
		if !ok || oauthTextUnsupported(text) {
			return nil, ErrOAuthNotPorted
		}
		names[index] = text
	}
	usernameValue, _ := object.Lookup("username")
	username, usernameText := usernameValue.(string)
	if !dto.IsASCII(email) || (usernameText && !dto.IsASCII(username)) {
		return nil, ErrOAuthNotPorted // Python's Unicode lower()/upper()
	}
	membership, refusal, err := s.signupMembership(ctx, object, collaborator, audit)
	if err != nil || refusal != nil {
		return refusal, err
	}
	if !usernameText || username == "" {
		return nil, ErrOAuthServer // data['username'].upper() / "The given username must be set"
	}
	hash, err := s.oauthUnusablePassword()
	if err != nil {
		return nil, err
	}
	user := domain.OAuthNewUser{ID: uuid.NewString(), Username: strings.ToUpper(username), Email: normalizeEmail(email),
		PasswordHash: hash, FirstName: names[0], LastName: names[1], Role: membership.role, CollaboratorRole: membership.collaboratorRole,
		ConnectedUserID: membership.connected, Permissions: membership.permissions, Joined: s.now().UTC()}
	if err = s.store.CreateUser(ctx, user, audit); errors.Is(err, domain.ErrOAuthUsernameConflict) {
		return nil, ErrOAuthServer // IntegrityError
	} else if err != nil {
		return nil, err
	}
	// The shared tail of the view: the partial user of the body's e-mail, the transfer.
	if err = s.store.DeletePartial(ctx, rawEmail, s.now().UTC(), audit); err != nil {
		return nil, err
	}
	if transfer {
		return nil, ErrOAuthServer // QuerySet.save: AttributeError
	}
	return s.welcomeNewUser(ctx, user, audit, language)
}

// existingAccountSignin is _existing_account_signin: one active account, its
// second factor, then the login response. Nothing is written or sent.
func (s *OAuthAccounts) existingAccountSignin(ctx context.Context, account domain.Account, count int, object domain.RequestObject,
	audit domain.LoginAuditContext) (any, error) {
	if count != 1 || !account.Active || account.Deleted {
		return OAuthAnswer{401, map[string]string{"error": "invalid credentials"}}, nil
	}
	enabled, secret, err := s.store.SecondFactor(ctx, account.ID)
	if err != nil {
		return nil, err
	}
	otp, present := object.Lookup("otp")
	switch err = s.secondFactor.Check(ctx, enabled, secret, otp, present); {
	case errors.Is(err, ErrOTPRequired):
		return OAuthAnswer{401, map[string]string{"msg": "OTP code required."}}, nil
	case errors.Is(err, ErrOTPInvalid):
		return OAuthAnswer{401, map[string]string{"msg": "OTP code not valid."}}, nil
	case err != nil:
		return nil, err
	}
	account.Password = ""
	result, err := s.signupSession(ctx, account, audit)
	if err != nil {
		return nil, err
	}
	return s.signupContent(ctx, account, result)
}

// welcomeNewUser is the end of oauth2_signup for a created account: tokens,
// build_login_response, the welcome e-mail and the signup-specific data.
func (s *OAuthAccounts) welcomeNewUser(ctx context.Context, user domain.OAuthNewUser, audit domain.LoginAuditContext,
	language string) (any, error) {
	account := domain.Account{ID: user.ID, Username: user.Username, Email: user.Email, Role: user.Role, Active: true,
		ConnectedUserID: user.ConnectedUserID, CollaboratorRole: user.CollaboratorRole}
	result, err := s.signupSession(ctx, account, audit)
	if err != nil {
		return nil, err
	}
	message, err := s.render(ctx, emailrender.AccountWelcome, s.brandContext(map[string]any{"first_name": user.FirstName,
		"last_name": user.LastName}), language)
	if err != nil {
		return nil, err
	}
	if err = s.sendMail(ctx, s.settings.WhitelabelName+" | Benvenuto", message, strings.ToLower(user.Email)); err != nil {
		return nil, err
	}
	return s.signupContent(ctx, account, result)
}

// signupSession issues the tokens and composes build_login_response as a JSON object.
func (s *OAuthAccounts) signupSession(ctx context.Context, account domain.Account, audit domain.LoginAuditContext) (map[string]any, error) {
	pair, err := s.tokens.Issue(account.ID, account.Role, account.Email)
	if err != nil {
		return nil, err
	}
	response, err := s.responses.Build(ctx, account, pair, audit)
	if err != nil {
		return nil, err
	}
	encoded, err := json.Marshal(response)
	if err != nil {
		return nil, err
	}
	decoder := json.NewDecoder(bytes.NewReader(encoded))
	decoder.UseNumber()
	var result map[string]any
	if err = decoder.Decode(&result); err != nil {
		return nil, err
	}
	return result, nil
}

// signupContent is _signup_content: for a collaborator the view replaces the
// composed association block with SportAssociationSerializer(...).data and
// sets requires_welcome.
func (s *OAuthAccounts) signupContent(ctx context.Context, account domain.Account, result map[string]any) (any, error) {
	if account.Role == domain.RoleCollaborator {
		if account.ConnectedUserID == nil {
			return nil, ErrOAuthServer // user.connected_user is None
		}
		profile, err := s.associations.Read(ctx, *account.ConnectedUserID)
		if err != nil {
			return nil, err
		}
		if profile == nil {
			return nil, domain.ErrAssociationNotFound
		}
		encoded, err := json.Marshal(profile)
		if err != nil {
			return nil, err
		}
		decoder := json.NewDecoder(bytes.NewReader(encoded))
		decoder.UseNumber()
		var block map[string]any
		if err = decoder.Decode(&block); err != nil {
			return nil, err
		}
		empty := func(value *string) bool { return value == nil || *value == "" }
		if empty(profile.Regulation) || empty(profile.Demand) {
			block["empty_sections"] = true
		}
		data, ok := result["user_data"].(map[string]any)
		if !ok {
			return nil, ErrLoginSessionInvariant
		}
		data["sport_association"] = block
		data["requires_welcome"] = false
	}
	return result, nil
}
