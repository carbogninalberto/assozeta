package service

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"math"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/emailrender"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"github.com/google/uuid"
)

const sendMailTask = "core.tasks.send_mail_async"

var (
	// ErrEmailRenderServer stands for the views' uncaught exceptions (500).
	ErrEmailRenderServer = errors.New("email render route raised")
	// ErrEmailRenderNotPorted refuses inputs outside the port (500 before any write).
	ErrEmailRenderNotPorted = refusal.New("email render route input not ported")
	// ErrEmailAlreadyPaid is payment_request's 412.
	ErrEmailAlreadyPaid = errors.New("email not sent, payment already paid.")
	// ErrEmailNoRecipient is invoice_send's 412.
	ErrEmailNoRecipient = errors.New("Nessuna email associata.")
	// ErrEmailNotAllowed is the views' PermissionDenied("User not allowed.").
	ErrEmailNotAllowed = errors.New("User not allowed.")
)

// EmailInputError is a DRF ValidationError raised with a string (400 [detail]).
type EmailInputError struct{ Detail string }

func (e *EmailInputError) Error() string { return e.Detail }

// EmailRenderSettings are the core.settings values the three views read.
type EmailRenderSettings struct {
	AppURL, WhitelabelName, DefaultFromEmail, DefaultTeamEmail string
	IsWhitelabel                                               bool
}

type EmailRenderRoutes struct {
	store     domain.EmailRenderStore
	publisher celery.Publisher
	settings  EmailRenderSettings
	now       func() time.Time
	random    func([]byte) (int, error)
}

func NewEmailRenderRoutes(store domain.EmailRenderStore, publisher celery.Publisher, settings EmailRenderSettings,
	now func() time.Time) *EmailRenderRoutes {
	return &EmailRenderRoutes{store: store, publisher: publisher, settings: settings, now: now, random: rand.Read}
}

func impersonationScope(identity domain.ResolvedIdentity) *string {
	if identity.ImpersonationAssociationID == "" {
		return nil
	}
	value := identity.ImpersonationAssociationID
	return &value
}

// render runs the template with the instance_email_brand of the current
// InstanceConfiguration row.
func (s *EmailRenderRoutes) render(ctx context.Context, name string, context map[string]any, language string) (string, error) {
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

// sendMail publishes send_mail_async.apply_async(kwargs={...}) in the views'
// key order; replyTo nil leaves the key out (collaborators_add).
func (s *EmailRenderRoutes) sendMail(ctx context.Context, subject, message, from string, recipients []any, association string, replyTo []any) error {
	kwargs := celery.Dict{
		{Key: "subject", Value: subject}, {Key: "message", Value: message}, {Key: "from_email", Value: from},
		{Key: "recipient_list", Value: recipients}, {Key: "html_message", Value: message}, {Key: "fail_silently", Value: false},
		{Key: "sport_association_id", Value: uuid.MustParse(association)},
	}
	if replyTo != nil {
		kwargs = append(kwargs, celery.Item{Key: "reply_to", Value: replyTo})
	}
	publish, cancel := context.WithTimeout(context.WithoutCancel(ctx), subscriptionUpdatePublishTimeout)
	defer cancel()
	_, err := s.publisher.Publish(publish, celery.Task{Name: sendMailTask, Kwargs: kwargs})
	return err
}

// requestAssociation is scoped_queryset(SportAssociation.objects).get(user=request.user):
// exactly one live row, else the 500 page.
func (s *EmailRenderRoutes) requestAssociation(ctx context.Context, identity domain.ResolvedIdentity) (domain.EmailRenderAssociation, error) {
	rows, err := s.store.Associations(ctx, identity.ScopeUser.ID, impersonationScope(identity))
	if err != nil {
		return domain.EmailRenderAssociation{}, err
	}
	if len(rows) != 1 {
		return domain.EmailRenderAssociation{}, ErrEmailRenderServer
	}
	return rows[0], nil
}

// --- collaborators/add ------------------------------------------------------------------------

// collaboratorEmail is check_email(data['email']) after the 'email' in data
// test: Django's validate_email on the raw value.
func collaboratorEmail(value any) (string, error) {
	invalid := &EmailInputError{Detail: "Email is not valid."}
	switch v := value.(type) {
	case string:
		if !utf8.ValidString(v) {
			return "", ErrEmailRenderNotPorted
		}
		if wtf8Surrogate(v) {
			return "", ErrEmailRenderServer // the request fails on the surrogate (500 page)
		}
		if strings.ContainsRune(v, 0) {
			return "", invalid // validate_email: NUL matches neither the user part, the quoted string nor the domain
		}
		if !dto.SupplierEmail(v) {
			return "", invalid
		}
		return v, nil
	case nil:
		return "", invalid
	case bool:
		if !v {
			return "", invalid
		}
		return "", ErrEmailRenderServer // "@" in True
	case json.Number:
		number, err := strconv.ParseFloat(string(v), 64)
		if err == nil && number == 0 {
			return "", invalid
		}
		return "", ErrEmailRenderServer // "@" in <number>
	case []any:
		for _, item := range v {
			if item == "@" {
				if len(v) > 320 {
					return "", invalid
				}
				return "", ErrEmailRenderServer // list.rsplit
			}
		}
		return "", invalid
	case domain.RequestObject:
		if _, found := v.Lookup("@"); found {
			if len(v) > 320 {
				return "", invalid
			}
			return "", ErrEmailRenderServer // dict.rsplit
		}
		return "", invalid
	}
	return "", ErrEmailRenderNotPorted
}

func wtf8Surrogate(text string) bool {
	for i := 0; i+2 < len(text); i++ {
		if text[i] == 0xed && text[i+1] >= 0xa0 && text[i+1] <= 0xbf {
			return true
		}
	}
	return false
}

// permissionsRoundTrip reports whether the stored jsonb reads back as the
// same json.dumps text, so the view's second save() logs no update entry:
// floats whose repr uses an exponent, and -0.0, come back from PostgreSQL
// as other numbers.
func permissionsRoundTrip(value any) bool {
	switch v := value.(type) {
	case json.Number:
		if !strings.ContainsAny(string(v), ".eE") {
			return true
		}
		number, err := strconv.ParseFloat(string(v), 64)
		if err != nil || math.IsInf(number, 0) {
			return true // PostgreSQL rejects Infinity: the insert fails as in Django
		}
		if number == 0 {
			return !math.Signbit(number)
		}
		exponent := math.Floor(math.Log10(math.Abs(number)))
		formatted := strconv.FormatFloat(math.Abs(number), 'e', -1, 64)
		if _, e, ok := strings.Cut(formatted, "e"); ok {
			if parsed, err := strconv.Atoi(e); err == nil {
				exponent = float64(parsed)
			}
		}
		return exponent >= -4 && exponent < 16
	case []any:
		for _, item := range v {
			if !permissionsRoundTrip(item) {
				return false
			}
		}
	case domain.RequestObject:
		for _, field := range v {
			if !permissionsRoundTrip(field.Value) {
				return false
			}
		}
	}
	return true
}

// AddCollaborator follows collaborators_add after request.data was parsed.
func (s *EmailRenderRoutes) AddCollaborator(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext,
	language string) error {
	var object domain.RequestObject
	switch data := body.(type) {
	case domain.RequestObject:
		object = data
	case []any:
		for _, item := range data {
			if item == "email" {
				return ErrEmailRenderServer // list['email']
			}
		}
		return &EmailInputError{Detail: "Email is required"}
	case string:
		if strings.Contains(data, "email") {
			return ErrEmailRenderServer // str['email']
		}
		return &EmailInputError{Detail: "Email is required"}
	default:
		return ErrEmailRenderServer // 'email' in None / number / bool
	}
	raw, present := object.Lookup("email")
	if !present {
		return &EmailInputError{Detail: "Email is required"}
	}
	email, err := collaboratorEmail(raw)
	if err != nil {
		return err
	}
	role, ok := dto.CollaboratorRole(object.Lookup("collaborator_role"))
	if !ok {
		return ErrEmailRenderServer
	}
	var stored *string
	if permissions, _ := object.Lookup("collaborator_permissions"); permissions != nil {
		if !permissionsRoundTrip(permissions) {
			return ErrEmailRenderNotPorted
		}
		if !dto.AssociateDraftStorable(permissions) {
			return ErrEmailRenderServer // PostgreSQL rejects the insert; nothing is written
		}
		text := string(dto.PythonJSONDumps(permissions))
		stored = &text
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	secret := make([]byte, 16)
	if _, err = s.random(secret); err != nil {
		return err
	}
	now := s.now().UTC().Truncate(time.Microsecond)
	invite := domain.CollaborationInvite{ID: uuid.NewString(), User: identity.ScopeUser.ID, Email: email, Token: hex.EncodeToString(secret),
		Role: role, Permissions: stored, Created: now, Expiration: now.Add(90 * 24 * time.Hour)}
	tx, err := s.store.BeginCollaboratorInvite(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = tx.CreateInvite(ctx, invite); err != nil {
		return err
	}
	// The create entry's index: the invitation has no association field and
	// its changes none either, so the actor's live association, if any.
	event := domain.CollaborationInviteAudit{Context: paymentCategoryAuditContext(identity, audit), At: now}
	association, err := tx.ActiveAssociationOf(ctx, identity.Actor.ID)
	if err != nil {
		return err
	}
	if association != nil {
		path := "actor.sport_association"
		event.AssociationID, event.ResolutionPath = association, &path
	}
	if err = tx.AppendInviteAudit(ctx, invite.ID, event); err != nil {
		return err
	}
	// request.user.sport_association (after the save): SportAssociation.objects.get(user=...).
	owner := identity.ScopeUser.ID
	if identity.ScopeUser.Role == domain.RoleCollaborator && identity.ScopeUser.ConnectedUserID != nil {
		owner = *identity.ScopeUser.ConnectedUserID
	}
	associations, err := tx.LiveAssociations(ctx, owner, nil)
	if err != nil {
		return err
	}
	if err = tx.Commit(); err != nil {
		return err
	}
	if len(associations) != 1 {
		return ErrEmailRenderServer // the invitation and its entry are kept
	}
	message, err := s.render(ctx, emailrender.CollaboratorInvite, map[string]any{
		"email": email, "token": invite.Token,
		"sport_association": map[string]any{"denomination": optionalValue(associations[0].Denomination)},
		"app_host":          s.settings.AppURL,
		"settings":          map[string]any{"WHITELABEL_NAME": s.settings.WhitelabelName, "IS_WHITELABEL": s.settings.IsWhitelabel},
	}, language)
	if err != nil {
		return err
	}
	return s.sendMail(ctx, s.settings.WhitelabelName+" | Invito a collaborare", message, s.settings.DefaultFromEmail, []any{email},
		associations[0].ID, nil)
}

// --- payment/<uid>/request --------------------------------------------------------------------

// RequestPayment follows payment_request; nothing is written.
func (s *EmailRenderRoutes) RequestPayment(ctx context.Context, identity domain.ResolvedIdentity, raw, groupHeader, language string) error {
	parsed, err := paymentInfoParseUUID(raw)
	if err != nil {
		return err
	}
	association, err := s.requestAssociation(ctx, identity)
	if err != nil {
		return err
	}
	payment, err := s.store.PaymentForRequest(ctx, parsed.String(), supplierGroup(groupHeader), impersonationScope(identity))
	if err != nil {
		return err
	}
	if payment == nil || payment.Association == nil {
		return domain.ErrEmailRenderNotFound
	}
	if *payment.Association != association.ID {
		return ErrEmailNotAllowed
	}
	if payment.Paid {
		return ErrEmailAlreadyPaid
	}
	recipients := []any{}
	if payment.HasAssociate && payment.AssociateEmail != nil {
		recipients = append(recipients, *payment.AssociateEmail)
	}
	if payment.User == nil || *payment.User != identity.ScopeUser.ID {
		recipients = append(recipients, identity.ScopeUser.Email)
	}
	description := ""
	if payment.Description != nil {
		description = *payment.Description + " "
	}
	switch payment.Subject {
	case 1:
		description = "per l'iscrizione "
	case 2:
		description = "per il corso "
	}
	if !payment.HasAssociate {
		return ErrEmailRenderServer // payment.associate.first_name on None
	}
	message, err := s.render(ctx, emailrender.PaymentPending, map[string]any{
		"recipient_name":          optionalText(payment.AssociateFirst) + " " + optionalText(payment.AssociateLast),
		"description":             description,
		"amount":                  emailrender.Decimal(payment.Amount),
		"application_name":        s.settings.WhitelabelName,
		"application_url":         s.settings.AppURL,
		"association_name":        optionalValue(payment.Denomination),
		"online_payments_enabled": payment.OnlinePayments != nil && *payment.OnlinePayments,
	}, language)
	if err != nil {
		return err
	}
	return s.sendMail(ctx, optionalText(payment.Denomination)+" | Pagamento in attesa", message, s.settings.DefaultTeamEmail, recipients,
		*payment.Association, []any{optionalValue(payment.OwnerEmail)})
}

// --- invoice/<uid>/send -----------------------------------------------------------------------

// SendInvoice follows invoice_send; nothing is written.
func (s *EmailRenderRoutes) SendInvoice(ctx context.Context, identity domain.ResolvedIdentity, raw, groupHeader, language string) error {
	parsed, err := paymentInfoParseUUID(raw)
	if err != nil {
		return err
	}
	association, err := s.requestAssociation(ctx, identity)
	if err != nil {
		return err
	}
	group, scope := supplierGroup(groupHeader), impersonationScope(identity)
	invoice, err := s.store.InvoiceForSend(ctx, parsed.String(), group, scope)
	if err != nil {
		return err
	}
	if invoice == nil {
		return domain.ErrEmailRenderNotFound
	}
	payment, err := s.store.InvoicePayment(ctx, invoice.ID, group, scope)
	if err != nil {
		return err
	}
	if invoice.Association != association.ID {
		return ErrEmailNotAllowed
	}
	if payment == nil {
		return domain.ErrEmailRenderNotFound
	}
	var email, name string
	switch {
	case payment.HasAssociate && payment.AssociateEmail != nil && *payment.AssociateEmail != "":
		email = *payment.AssociateEmail
		name = optionalText(payment.AssociateFirst) + " " + optionalText(payment.AssociateLast)
	case payment.HasUser && payment.UserEmail != nil && *payment.UserEmail != "":
		email = *payment.UserEmail
		name = optionalText(payment.UserFirst) + " " + optionalText(payment.UserLast)
	case payment.HasSupplier && payment.SupplierEmail != nil && *payment.SupplierEmail != "":
		email = *payment.SupplierEmail
		name = optionalText(payment.SupplierName)
	default:
		return ErrEmailNoRecipient
	}
	if !invoice.HasDocumentPDF || invoice.DocumentToken == nil || payment.PaymentDate == nil {
		return ErrEmailRenderServer // document_pdf.token / payment_date.strftime on None
	}
	paid := payment.PaymentDate.UTC()
	if paid.Year() < 1000 {
		return ErrEmailRenderNotPorted
	}
	day := paid.Format("02/01/2006")
	membership, err := strconv.ParseFloat(invoice.MembershipFee, 64)
	if err != nil {
		return err
	}
	activity, err := strconv.ParseFloat(invoice.ActivityFee, 64)
	if err != nil {
		return err
	}
	var number any
	numberText := "None"
	if invoice.Number != nil {
		number, numberText = *invoice.Number, strconv.FormatInt(*invoice.Number, 10)
	}
	downloadURL := s.settings.AppURL + "/api/document/retrieve/" + invoice.ID + "?download=true&token=" + *invoice.DocumentToken
	message, err := s.render(ctx, emailrender.PaymentReceipt, map[string]any{
		"recipient_name":   name,
		"amount":           membership + activity,
		"receipt_number":   number,
		"payment_date":     day,
		"description":      optionalValue(invoice.Description),
		"download_url":     downloadURL,
		"association_name": optionalValue(association.Denomination),
	}, language)
	if err != nil {
		return err
	}
	subject := optionalText(association.Denomination) + " | Ricevuta di pagamento n. " + numberText + " - " + day
	return s.sendMail(ctx, subject, message, s.settings.DefaultTeamEmail, []any{email}, association.ID,
		[]any{optionalValue(association.OwnerEmail)})
}
