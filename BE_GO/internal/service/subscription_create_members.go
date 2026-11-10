package service

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/djangoauth"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/emailrender"
	"github.com/google/uuid"
)

// create_subscription's new member account, the signature and the athlete
// e-mail (subscription-create.md).

// SubscriptionCreateAccounts are the dependencies of the new member account
// and its welcome e-mail (AuthUtils.send_password_welcome_email).
type SubscriptionCreateAccounts struct {
	Passwords OAuthPasswordEncoder
	Settings  OAuthAccountSettings
	// InstanceConfig is the e-mail brand's instance configuration.
	InstanceConfig func(ctx context.Context) (domain.EmailRenderInstanceConfig, error)
}

// WithAccounts enables new member accounts; without it they are not ported.
func (s *SubscriptionCreates) WithAccounts(accounts SubscriptionCreateAccounts) *SubscriptionCreates {
	s.accounts = &accounts
	return s
}

// WithSignatures enables the signature upload (set_signature_from_base64).
func (s *SubscriptionCreates) WithSignatures(signatures *SubscriptionSigns) *SubscriptionCreates {
	s.signatures = signatures
	return s
}

var (
	// ErrSubscriptionCreateSignatureInvalid is InvalidSignatureError (400).
	ErrSubscriptionCreateSignatureInvalid = errors.New("subscription create: invalid signature")
	// ErrSubscriptionCreateStorage is StorageUnavailableError (503).
	ErrSubscriptionCreateStorage = errors.New("subscription create: storage unavailable")
)

// subscriptionCreateMember is the user create_subscription continues with.
type subscriptionCreateMember struct {
	member  domain.SubscriptionCreateMember
	welcome *celery.Task // the welcome e-mail of a new account
}

func subscriptionCreateTokenHex(n int) (string, error) {
	raw := make([]byte, n)
	if _, err := rand.Read(raw); err != nil {
		return "", err
	}
	return hex.EncodeToString(raw), nil
}

// newMember is `new_user_account.is_valid() and new_user_account.save().new_member`:
// the user with new_member_info's e-mail, else a new account (nil: request.user).
func (s *SubscriptionCreates) newMember(ctx context.Context, tx domain.SubscriptionCreateTransaction, data domain.RequestObject,
	settings *domain.SubscriptionCreateSettings, request SubscriptionCreateRequest) (*subscriptionCreateMember, error) {
	accountValue, _ := data.Lookup("new_user_account")
	account, ok := accountValue.(domain.RequestObject)
	if !ok {
		return nil, nil // is_valid() is False
	}
	flagValue, present := account.Lookup("new_member")
	if !present {
		return nil, nil
	}
	flag, code := dtoBoolean(flagValue)
	if code != "" {
		if _, isNumber := flagValue.(json.Number); isNumber {
			// Python's 1.0 == 1 in BooleanField.TRUE_VALUES is not ported.
			return nil, domain.ErrSubscriptionCreateNotPorted
		}
		return nil, nil
	}
	if !flag {
		return nil, nil
	}
	infoValue, present := account.Lookup("new_member_info")
	if !present {
		return nil, pythonKeyError("new_member_info")
	}
	info, ok := infoValue.(domain.RequestObject)
	if !ok {
		return nil, domain.ErrSubscriptionCreateServer // initial_data['email'] on a non-dict
	}
	emailValue, present := info.Lookup("email")
	if !present {
		return nil, pythonKeyError("email")
	}
	email, ok := emailValue.(string)
	if !ok {
		return nil, domain.ErrSubscriptionCreateNotPorted
	}
	existing, err := tx.MemberByEmail(ctx, email)
	if err != nil {
		return nil, err
	}
	if existing != nil {
		return &subscriptionCreateMember{member: *existing}, nil
	}
	if s.accounts == nil {
		return nil, domain.ErrSubscriptionCreateNotPorted
	}
	// UserSerializer(data=new_member_info).is_valid(raise_exception=True).
	fields := map[string][]string{}
	texts := map[string]*string{}
	for _, field := range []dto.SupplierField{{Name: "first_name", Max: 150, Blank: true}, {Name: "last_name", Max: 150, Blank: true},
		{Name: "email", Max: 254, Blank: true, Email: true}, {Name: "phone", Max: 20, Null: true, Blank: true}} {
		value, present := info.Lookup(field.Name)
		if !present {
			continue
		}
		text, codes := dto.SubscriptionCreateUserText(field, value)
		if len(codes) > 0 {
			fields[field.Name] = codes
			continue
		}
		texts[field.Name] = text
	}
	// username = CharField(required=False); role is the model's ChoiceField (str(value) among the role keys).
	sentUsername, hasUsername := info.Lookup("username")
	if hasUsername {
		if _, codes := dto.SubscriptionCreateUserText(dto.SupplierField{Name: "username"}, sentUsername); len(codes) > 0 {
			fields["username"] = codes
		}
	}
	accountRole := domain.RoleAthlete
	if role, present := info.Lookup("role"); present {
		if role == nil {
			_, codes := dto.SubscriptionCreateUserText(dto.SupplierField{Name: "role"}, nil)
			fields["role"] = codes
		} else {
			text, isText := role.(string)
			if !isText {
				text = dto.PythonRepr(role)
			}
			switch text {
			case "1":
				accountRole = domain.RoleAssociation
			case "2":
			case "3":
				accountRole = domain.RoleCollaborator
			default:
				fields["role"] = []string{"choice:" + text}
			}
		}
	}
	if len(fields) > 0 {
		return nil, &domain.SubscriptionCreateInvalid{Fields: fields}
	}
	if hasUsername {
		// create_user(**validated_data, username=...) with a validated username: TypeError.
		return nil, pythonFailure("django.contrib.auth.models.UserManager.create_user() got multiple values for keyword argument 'username'")
	}
	value := func(name string) string {
		if text := texts[name]; text != nil {
			return *text
		}
		return ""
	}
	first, last := value("first_name"), value("last_name")
	token, err := subscriptionCreateTokenHex(10)
	if err != nil {
		return nil, err
	}
	suffix, err := secretsChoice(transferAlphabet, 8)
	if err != nil {
		return nil, err
	}
	password, err := secretsChoice(transferAlphabet, 8)
	if err != nil {
		return nil, err
	}
	hash, err := s.accounts.Passwords.Encode(ctx, password)
	if err != nil {
		return nil, err
	}
	// first_name[:3] + last_name[:3] + the random part upper-cased.
	username := pythonPrefix(first, 3) + pythonPrefix(last, 3) + strings.ToUpper(suffix)
	user := domain.SubscriptionCreateNewUser{ID: uuid.NewString(), TokenUsername: djangoauth.NormalizeUsername(token), Username: username,
		Email: djangoauth.NormalizeEmail(value("email")), PasswordHash: hash, FirstName: first, LastName: last, Phone: texts["phone"],
		Role: accountRole, Joined: s.now().UTC().Truncate(time.Microsecond)}
	if err = tx.InsertNewUser(ctx, user, request.Audit); err != nil {
		return nil, err
	}
	welcome, err := s.welcomeMail(ctx, user, password, settings, request)
	if err != nil {
		return nil, err
	}
	return &subscriptionCreateMember{member: domain.SubscriptionCreateMember{ID: user.ID, Role: accountRole, StartDay: 1, StartMonth: 1,
		EndDay: 31, EndMonth: 12}, welcome: welcome}, nil
}

// welcomeMail is AuthUtils.send_password_welcome_email's send_mail_async message.
func (s *SubscriptionCreates) welcomeMail(ctx context.Context, user domain.SubscriptionCreateNewUser, password string,
	settings *domain.SubscriptionCreateSettings, request SubscriptionCreateRequest) (*celery.Task, error) {
	stored, err := s.accounts.InstanceConfig(ctx)
	if err != nil {
		return nil, err
	}
	var config *emailrender.InstanceConfig
	if stored.Found {
		config = &emailrender.InstanceConfig{SelfHosted: stored.SelfHosted, PrimaryColor: stored.PrimaryColor, Name: stored.Name,
			LogoPath: stored.LogoPath, UpdatedAt: stored.UpdatedAt}
	}
	options := s.accounts.Settings
	brand, err := emailrender.Brand(config, emailrender.Settings{AppURL: options.AppURL, WhitelabelName: options.WhitelabelName,
		IsWhitelabel: options.IsWhitelabel})
	if err != nil {
		return nil, err
	}
	var denomination any
	if settings.Denomination != nil {
		denomination = *settings.Denomination
	}
	html, err := emailrender.Render(emailrender.AccountWelcomePassword, map[string]any{"first_name": user.FirstName, "last_name": user.LastName,
		"username": user.Username, "email": user.Email, "password": password, "sport_association": map[string]any{"denomination": denomination},
		"app_host": options.AppURL, "settings": map[string]any{"WHITELABEL_NAME": options.WhitelabelName, "IS_WHITELABEL": options.IsWhitelabel}},
		emailrender.Options{Language: request.Language, Brand: brand})
	if err != nil {
		return nil, err
	}
	return &celery.Task{Name: sendMailTask, Kwargs: celery.Dict{
		{Key: "subject", Value: options.WhitelabelName + " | Benvenuto"}, {Key: "message", Value: html},
		{Key: "from_email", Value: options.DefaultFromEmail}, {Key: "recipient_list", Value: []any{user.Email}},
		{Key: "html_message", Value: html}, {Key: "fail_silently", Value: false},
	}}, nil
}

// athleteMail is the send_email_template message create_subscription queues
// when its user is an athlete.
func (s *SubscriptionCreates) athleteMail(settings *domain.SubscriptionCreateSettings, first, last *string) *celery.Task {
	var denomination any
	if settings.Denomination != nil {
		denomination = *settings.Denomination
	}
	name := func(value *string) any {
		if value == nil {
			return nil
		}
		return *value
	}
	options := OAuthAccountSettings{}
	if s.accounts != nil {
		options = s.accounts.Settings
	}
	return &celery.Task{Name: "send_email_template", Kwargs: celery.Dict{
		{Key: "recipient_list", Value: []any{settings.OwnerEmail}},
		{Key: "subject", Value: "[" + subscriptionCreateValue(settings.Denomination) + "] Ricordati di approvare l'iscrizione di " +
			subscriptionCreateValue(first) + " " + subscriptionCreateValue(last)},
		{Key: "template", Value: "email/account/email_new_subscription.html"},
		{Key: "data", Value: celery.Dict{{Key: "athlete_first_name", Value: name(first)}, {Key: "athlete_last_name", Value: name(last)},
			{Key: "sport_association", Value: celery.Dict{{Key: "denomination", Value: denomination}}}, {Key: "app_host", Value: options.AppURL},
			{Key: "settings", Value: celery.Dict{{Key: "WHITELABEL_NAME", Value: options.WhitelabelName},
				{Key: "IS_WHITELABEL", Value: options.IsWhitelabel}}}}},
		{Key: "sport_association_id", Value: uuid.MustParse(settings.AssociationID)},
	}}
}

// uploadSignature is set_signature_from_base64 for the new subscription: the
// stored name and public URL, or the URL itself.
func (s *SubscriptionCreates) uploadSignature(ctx context.Context, subscription, data string, request SubscriptionCreateRequest) (url, key *string, err error) {
	if strings.HasPrefix(data, "http://") || strings.HasPrefix(data, "https://") {
		return &data, nil, nil
	}
	if s.signatures == nil {
		return nil, nil, domain.ErrSubscriptionCreateNotPorted
	}
	name, public, err := s.signatures.upload(ctx, subscription, data)
	switch {
	case errors.Is(err, ErrSubscriptionSignInvalid):
		return nil, nil, ErrSubscriptionCreateSignatureInvalid
	case errors.Is(err, ErrSubscriptionSignStorage):
		return nil, nil, ErrSubscriptionCreateStorage
	case errors.Is(err, ErrSubscriptionSignNotPorted):
		return nil, nil, domain.ErrSubscriptionCreateNotPorted
	case err != nil:
		return nil, nil, err
	}
	if request.StorageKeys != nil {
		*request.StorageKeys = append(*request.StorageKeys, name)
	}
	return public, &name, nil
}

// cleanupStorage is cleanup_storage_keys after a failed create_subscription.
func (s *SubscriptionCreates) cleanupStorage(ctx context.Context, keys []string) {
	if s.signatures == nil {
		return
	}
	for i := range keys {
		s.signatures.cleanup(ctx, &keys[i])
	}
}
