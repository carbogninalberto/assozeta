package service

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"math/big"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/djangoauth"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/emailrender"
	"github.com/google/uuid"
)

var (
	ErrSubscriptionTransferNotFound = errors.New("subscription not found")
	ErrSubscriptionTransferNotOwner = errors.New("not the owner of the subscription")
	ErrSubscriptionTransferPending  = errors.New("pending transfer")
	ErrSubscriptionTransferSelf     = errors.New("transfer to yourself")
	// ErrSubscriptionTransferNoRecipient is Response(status=400) without a body.
	ErrSubscriptionTransferNoRecipient = errors.New("recipient not found")
	ErrSubscriptionTransferNoEmail     = errors.New("email is required")
	// ErrSubscriptionTransferFailed is an exception the view does not handle (the 500 page).
	ErrSubscriptionTransferFailed = errors.New("subscription transfer failed")
	// ErrSubscriptionTransferNotPorted refuses, before any write, a transfer
	// whose save the port does not reproduce (see subscription-transfer.md).
	ErrSubscriptionTransferNotPorted = errors.New("subscription transfer path not ported")
)

// subscriptionTransferValidity is default_expires_at's 30 days.
const subscriptionTransferValidity = 30 * 24 * time.Hour

// SubscriptionTransferRequest is the request data the view reads.
type SubscriptionTransferRequest struct {
	UID, Group string
	Body       any
	BodyError  error // request.data raising (parse errors) when it is first read
	Audit      domain.LoginAuditContext
	Language   string
}

// SubscriptionTransferResult is SubscriptionTransferCreateSerializer(...).data.
type SubscriptionTransferResult struct {
	Subscription, ExpiresAt, Requester, Recipient, Token string
}

// MarshalJSON keeps the serializer's field order.
func (r SubscriptionTransferResult) MarshalJSON() ([]byte, error) {
	var out strings.Builder
	out.WriteByte('{')
	for i, item := range [][2]string{{"subscription", r.Subscription}, {"expires_at", r.ExpiresAt}, {"requester", r.Requester},
		{"recipient", r.Recipient}, {"token", r.Token}} {
		if i > 0 {
			out.WriteByte(',')
		}
		key, _ := json.Marshal(item[0])
		value, _ := json.Marshal(item[1])
		out.Write(key)
		out.WriteByte(':')
		out.Write(value)
	}
	out.WriteString(`,"status":2}`)
	return []byte(out.String()), nil
}

type SubscriptionTransfers struct {
	store     domain.SubscriptionTransferStore
	notifier  *SubscriptionNotifier
	publisher celery.Publisher
	passwords OAuthPasswordEncoder
	settings  OAuthAccountSettings
	now       func() time.Time
	random    func(alphabet string, n int) (string, error)
}

func NewSubscriptionTransfers(store domain.SubscriptionTransferStore, notifier *SubscriptionNotifier, publisher celery.Publisher,
	passwords OAuthPasswordEncoder, settings OAuthAccountSettings, now func() time.Time) *SubscriptionTransfers {
	return &SubscriptionTransfers{store: store, notifier: notifier, publisher: publisher, passwords: passwords, settings: settings, now: now,
		random: secretsChoice}
}

// secretsChoice is ”.join(secrets.choice(alphabet) for i in range(n)).
func secretsChoice(alphabet string, n int) (string, error) {
	out := make([]byte, n)
	limit := big.NewInt(int64(len(alphabet)))
	for i := range out {
		index, err := rand.Int(rand.Reader, limit)
		if err != nil {
			return "", err
		}
		out[i] = alphabet[index.Int64()]
	}
	return string(out), nil
}

const transferAlphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

func (s *SubscriptionTransfers) clock() time.Time { return s.now().UTC().Truncate(time.Microsecond) }

// ParseUUID is is_valid_uuid: a 400 with Python's message.
func (s *SubscriptionTransfers) ParseUUID(raw string) (string, error) {
	return messageReadsParseUUID(raw)
}

// transferRecipientID is the UUIDField conversion of User.objects.filter(user_id=value):
// str -> UUID(hex=...), int (bool included) -> UUID(int=...); anything else raises.
func transferRecipientID(value any) (string, bool) {
	switch v := value.(type) {
	case string:
		id, err := paymentInfoParseUUID(v)
		if err != nil {
			return "", false
		}
		return id.String(), true
	case bool:
		if v {
			return "00000000-0000-0000-0000-000000000001", true
		}
		return "00000000-0000-0000-0000-000000000000", true
	case json.Number:
		number, ok := new(big.Int).SetString(v.String(), 10)
		if !ok || number.Sign() < 0 || number.BitLen() > 128 {
			return "", false
		}
		var raw [16]byte
		number.FillBytes(raw[:])
		return uuid.UUID(raw).String(), true
	}
	return "", false
}

// transferEmail is UserManager.normalize_email(email): (email or ”),
// strip() and the domain lower-cased when it has an '@'.
func transferEmail(value any) (string, bool) {
	if pythonFalsy(value) {
		return "", true
	}
	text, ok := value.(string)
	if !ok {
		return "", false // str methods on another type: AttributeError
	}
	stripped := dto.PythonStrip(text)
	at := strings.LastIndex(stripped, "@")
	if at < 0 {
		return text, true
	}
	return stripped[:at] + "@" + djangoauth.PythonLower(stripped[at+1:]), true
}

func asciiText(text string) bool {
	for i := 0; i < len(text); i++ {
		if text[i] >= utf8.RuneSelf {
			return false
		}
	}
	return true
}

// pythonPrefix is text[:n] (code points).
func pythonPrefix(text string, n int) string {
	count := 0
	for i := range text {
		if count == n {
			return text[:i]
		}
		count++
	}
	return text
}

// transferNames are the tutor's names (the main tutor) or the associate's.
func transferNames(associate domain.SubscriptionTransferPerson, tutor *domain.SubscriptionTransferTutor) (*string, *string) {
	if tutor != nil {
		return tutor.Tutor.FirstName, tutor.Tutor.LastName
	}
	return associate.FirstName, associate.LastName
}

// refused decides, read-only and before the view's first write, whether the
// request reaches a save the port does not reproduce.
func (s *SubscriptionTransfers) refused(ctx context.Context, identity domain.ResolvedIdentity, row domain.SubscriptionTransferRow,
	request SubscriptionTransferRequest, at time.Time) (bool, error) {
	pending, err := s.store.Pending(ctx, row.ID, &at)
	if err != nil || pending || request.BodyError != nil {
		return false, err
	}
	object, ok := request.Body.(domain.RequestObject)
	if !ok {
		return false, nil
	}
	if value, _ := object.Lookup("recipient"); value != nil {
		if text, isText := value.(string); isText && text == identity.ScopeUser.ID {
			return false, nil
		}
		id, valid := transferRecipientID(value)
		if !valid {
			return false, nil
		}
		found, err := s.store.Recipient(ctx, id, identity.ScopeUser.ID)
		if err != nil || found == nil {
			return false, err
		}
		// Subscription.save() fills empty dates from the owner's settings.
		return !row.DatesSet, nil
	}
	email, _ := object.Lookup("email")
	if email == nil || row.AssociateID == nil {
		return false, nil
	}
	associate, err := s.store.Associate(ctx, *row.AssociateID)
	if err != nil || associate == nil {
		return false, err
	}
	tutor, err := s.store.MainTutor(ctx, associate.ID)
	if err != nil {
		return false, err
	}
	first, last := transferNames(*associate, tutor)
	if first == nil || last == nil {
		return false, nil
	}
	return !row.DatesSet && identity.ScopeUser.Role != domain.RoleAthlete, nil
}

// Transfer follows subscription_transfer.
func (s *SubscriptionTransfers) Transfer(ctx context.Context, identity domain.ResolvedIdentity, request SubscriptionTransferRequest) (
	*SubscriptionTransferResult, error) {
	id, err := s.ParseUUID(request.UID)
	if err != nil {
		return nil, err
	}
	row, err := s.store.Subscription(ctx, id, paymentCategoryAssociationScope(identity), supplierGroup(request.Group))
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, ErrSubscriptionTransferNotFound
	}
	requester := identity.ScopeUser.ID
	if row.UserID != requester {
		return nil, ErrSubscriptionTransferNotOwner
	}
	now := s.clock()
	if refuse, err := s.refused(ctx, identity, *row, request, now); err != nil {
		return nil, err
	} else if refuse {
		return nil, ErrSubscriptionTransferNotPorted
	}
	if err = s.store.ExpireTransfers(ctx, now); err != nil {
		return nil, err
	}
	pending, err := s.store.Pending(ctx, row.ID, nil)
	if err != nil {
		return nil, err
	}
	if pending {
		return nil, ErrSubscriptionTransferPending
	}
	if request.BodyError != nil {
		return nil, request.BodyError
	}
	object, ok := request.Body.(domain.RequestObject)
	if !ok {
		return nil, ErrSubscriptionTransferFailed // request.data.get: AttributeError
	}
	audit := paymentCategoryAuditContext(identity, request.Audit)
	request.Audit = audit
	var associate *domain.SubscriptionTransferPerson
	loadAssociate := func() error {
		if associate != nil {
			return nil
		}
		if row.AssociateID == nil {
			return ErrSubscriptionTransferFailed // None.<attribute>: AttributeError
		}
		found, err := s.store.Associate(ctx, *row.AssociateID)
		if err != nil {
			return err
		}
		if found == nil {
			return ErrSubscriptionTransferFailed
		}
		associate = found
		return nil
	}
	mainTutor := func() (*domain.SubscriptionTransferTutor, error) {
		tutor, err := s.store.MainTutor(ctx, associate.ID)
		if err != nil || tutor == nil || tutor.Primary {
			return tutor, err
		}
		return tutor, s.store.MakePrimary(ctx, *tutor, associate.AssociationID, audit, s.clock())
	}
	var recipient string
	if value, _ := object.Lookup("recipient"); value != nil {
		if text, isText := value.(string); isText && text == requester {
			return nil, ErrSubscriptionTransferSelf
		}
		userID, valid := transferRecipientID(value)
		if !valid {
			return nil, ErrSubscriptionTransferFailed // UUIDField: ValidationError
		}
		found, err := s.store.Recipient(ctx, userID, requester)
		if err != nil {
			return nil, err
		}
		if found == nil {
			return nil, ErrSubscriptionTransferNoRecipient
		}
		recipient = *found
	} else {
		email, _ := object.Lookup("email")
		if email == nil {
			return nil, ErrSubscriptionTransferNoEmail
		}
		if err = loadAssociate(); err != nil {
			return nil, err
		}
		tutor, err := mainTutor()
		if err != nil {
			return nil, err
		}
		recipient, err = s.createAccount(ctx, identity, *associate, tutor, email, request)
		if err != nil {
			return nil, err
		}
	}
	if err = loadAssociate(); err != nil {
		return nil, err
	}
	name := dto.PythonStrip(optionalText(associate.FirstName) + " " + optionalText(associate.LastName))
	message := "l'associazione " + optionalText(row.Denomination) + " ha trasferito l'iscrizione " + name + " al tuo account"
	if err = s.notifier.Deliver(ctx, recipient, "subscription", message, row.AssociationID, audit); err != nil {
		return nil, err
	}
	created := s.clock()
	transfer := domain.SubscriptionTransferCreated{ID: uuid.NewString(), SubscriptionID: row.ID, AssociationID: row.AssociationID,
		Requester: requester, Recipient: recipient, Token: uuid.NewString(), Created: created, Expires: created.Add(subscriptionTransferValidity)}
	if err = s.store.CreateTransfer(ctx, transfer, audit); err != nil {
		return nil, err
	}
	// subscription.save() computes the season range first: settings that name no date raise here, after the
	// notification and the transfer were written (the view is not atomic).
	season, err := s.store.SubscriptionSaveSeason(ctx, row.ID)
	if err != nil {
		return nil, err
	}
	if err = modelHooksSubscriptionSave(ctx, season); err != nil {
		return nil, err
	}
	if err = s.store.SaveSubscriptionUser(ctx, *row, recipient, audit, s.clock()); err != nil {
		return nil, err
	}
	if err = s.store.SaveAssociateUser(ctx, *associate, recipient, audit, s.clock()); err != nil {
		return nil, err
	}
	tutor, err := mainTutor()
	if err != nil {
		return nil, err
	}
	if tutor != nil {
		if err = s.store.SaveAssociateUser(ctx, tutor.Tutor, recipient, audit, s.clock()); err != nil {
			return nil, err
		}
	}
	accepted := s.clock()
	if err = s.store.AcceptTransfer(ctx, transfer, accepted, audit); err != nil {
		return nil, err
	}
	return &SubscriptionTransferResult{Subscription: row.ID, ExpiresAt: dto.DRFUTCDateTime(accepted), Requester: requester,
		Recipient: recipient, Token: transfer.Token}, nil
}

// createAccount is the e-mail branch: create_user with a random username and
// password, then AuthUtils.send_password_welcome_email.
func (s *SubscriptionTransfers) createAccount(ctx context.Context, identity domain.ResolvedIdentity, associate domain.SubscriptionTransferPerson,
	tutor *domain.SubscriptionTransferTutor, email any, request SubscriptionTransferRequest) (string, error) {
	first, last := transferNames(associate, tutor)
	if first == nil || last == nil {
		return "", ErrSubscriptionTransferFailed // None[:3]: TypeError
	}
	suffix, err := s.random(transferAlphabet, 8)
	if err != nil {
		return "", err
	}
	password, err := s.random(transferAlphabet, 8)
	if err != nil {
		return "", err
	}
	// str(username).upper(), then create_user's normalize_username (NFKC).
	username := djangoauth.NormalizeUsername(djangoauth.PythonUpper(pythonPrefix(*first, 3) + pythonPrefix(*last, 3) + suffix))
	address, ok := transferEmail(email)
	if !ok {
		return "", ErrSubscriptionTransferFailed
	}
	hash, err := s.passwords.Encode(ctx, password)
	if err != nil {
		return "", err
	}
	user := domain.OAuthNewUser{ID: uuid.NewString(), Username: username, Email: address, PasswordHash: hash, FirstName: *first,
		LastName: *last, Role: domain.RoleAthlete, CollaboratorRole: 1, Joined: s.clock()}
	if err = s.store.CreateUser(ctx, user, request.Audit); err != nil {
		return "", err
	}
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return "", &domain.PermissionDenied{Detail: "User is not a sport association"}
	}
	association, err := s.store.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return "", err
	}
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
	var denomination any
	if association.Denomination != nil {
		denomination = *association.Denomination
	}
	html, err := emailrender.Render(emailrender.AccountWelcomePassword, map[string]any{"first_name": *first, "last_name": *last,
		"username": username, "email": address, "password": password, "sport_association": map[string]any{"denomination": denomination},
		"app_host": s.settings.AppURL, "settings": map[string]any{"WHITELABEL_NAME": s.settings.WhitelabelName,
			"IS_WHITELABEL": s.settings.IsWhitelabel}}, emailrender.Options{Language: request.Language, Brand: brand})
	if err != nil {
		return "", err
	}
	publish, cancel := context.WithTimeout(context.WithoutCancel(ctx), subscriptionUpdatePublishTimeout)
	defer cancel()
	if _, err = s.publisher.Publish(publish, celery.Task{Name: sendMailTask, Kwargs: celery.Dict{
		{Key: "subject", Value: s.settings.WhitelabelName + " | Benvenuto"}, {Key: "message", Value: html},
		{Key: "from_email", Value: s.settings.DefaultFromEmail}, {Key: "recipient_list", Value: []any{address}},
		{Key: "html_message", Value: html}, {Key: "fail_silently", Value: false},
	}}); err != nil {
		return "", err
	}
	return user.ID, nil
}
