package service

import (
	"context"
	"errors"
	"log/slog"
	"slices"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
)

type MessageDelivery func(ctx context.Context, association string, message domain.OutgoingMessage, audit domain.LoginAuditContext) (bool, error)

type MessageTemplates struct {
	store    domain.MessageTemplateStore
	delivery map[string]MessageDelivery
	now      func() time.Time
}

func NewMessageTemplates(store domain.MessageTemplateStore, delivery map[string]MessageDelivery, now func() time.Time) *MessageTemplates {
	return &MessageTemplates{store: store, delivery: delivery, now: now}
}

type OutgoingMessageOptions struct {
	RecipientName string                   `json:"recipient_name"`
	Templates     []domain.MessageTemplate `json:"templates"`
}

type OutgoingMessageResult struct {
	domain.OutgoingMessage
	CanSend         bool   `json:"can_send"`
	CanOpenWhatsApp bool   `json:"can_open_whatsapp"`
	Warning         string `json:"warning"`
	WhatsAppURL     string `json:"whatsapp_url,omitempty"`
}

var ErrOutgoingMessageUsed = errors.New("Questa anteprima è già stata utilizzata.")
var ErrOutgoingRecipientChanged = errors.New("Il recapito è cambiato. Crea una nuova anteprima.")

const sendConfirmationWindow = 10 * time.Minute

var outgoingWarnings = map[string]string{
	"discarded": "Anteprima scartata. L’invio automatico è stato annullato.",
	"manual":    "Invio automatico saltato. Completa l’invio in WhatsApp.",
	"skipped":   "Invio automatico saltato. Nessuna integrazione è disponibile per questo canale.",
	"failed":    "Il servizio di invio non ha confermato la richiesta.",
	"unknown":   "Non è stato possibile confermare l’invio. Verifica la consegna prima di inviare un altro messaggio.",
}

func (s *MessageTemplates) result(row domain.OutgoingMessage) OutgoingMessageResult {
	direct := s.delivery[row.Channel] != nil
	result := OutgoingMessageResult{OutgoingMessage: row, CanSend: direct, CanOpenWhatsApp: row.Channel == "whatsapp" && !direct,
		Warning: outgoingWarnings[row.Status]}
	if row.Status == "manual" {
		result.WhatsAppURL = dto.WhatsAppMessageURL(row)
	}
	return result
}

func (s *MessageTemplates) Admit(identity domain.ResolvedIdentity, outgoing bool) error {
	if _, err := paymentCategoryOwner(identity, denial("Accesso negato.")); err != nil {
		return err
	}
	if !identity.ScopeUser.Active || identity.ScopeUser.Deleted {
		return denial("Accesso negato.")
	}
	if outgoing && identity.Collaborator && identity.Effective.CollaboratorRole != domain.CollaboratorFull {
		grants, err := persistedGrants(identity.Effective.Permissions)
		if err != nil {
			return err
		}
		const permission = "association.personas.read"
		if !slices.Contains(grants, permission) {
			return denial("Missing permission: " + permission)
		}
	}
	return nil
}

func (s *MessageTemplates) begin(ctx context.Context, identity domain.ResolvedIdentity, lock bool) (domain.MessageTemplateTransaction, string, string, error) {
	tx, err := s.store.BeginMessageTemplates(ctx)
	if err != nil {
		return nil, "", "", err
	}
	id, name, err := tx.Association(ctx, identity.ScopeUser.ID, paymentCategoryAssociationScope(identity), lock)
	if err != nil {
		_ = tx.Rollback()
		if errors.Is(err, domain.ErrAssociationNotFound) {
			err = denial("Accesso negato.")
		}
		return nil, "", "", err
	}
	return tx, id, name, nil
}

func seedMessageTemplates(ctx context.Context, tx domain.MessageTemplateTransaction, association string, at time.Time) error {
	seeded, err := tx.HasTemplates(ctx, association)
	if err != nil || seeded {
		return err
	}
	for _, channel := range []string{"email", "whatsapp", "sms"} {
		row := domain.MessageTemplate{ID: uuid.NewString(), Channel: channel, Locale: "it", Name: "Messaggio di saluto",
			Body: "Ciao {{customer_name}}, ti scriviamo da {{association_name}}.", Enabled: true, IsDefault: true}
		if channel == "email" {
			row.Subject = "Un messaggio da {{association_name}}"
		}
		if err := tx.InsertTemplate(ctx, association, row, domain.LoginAuditContext{}, at); err != nil {
			return err
		}
	}
	return nil
}

func (s *MessageTemplates) List(ctx context.Context, identity domain.ResolvedIdentity) ([]domain.MessageTemplate, error) {
	tx, association, _, err := s.begin(ctx, identity, true)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	if err := seedMessageTemplates(ctx, tx, association, s.now()); err != nil {
		return nil, err
	}
	rows, err := tx.Templates(ctx, association, false)
	if err != nil {
		return nil, err
	}
	return rows, tx.Commit()
}

func (s *MessageTemplates) Save(ctx context.Context, identity domain.ResolvedIdentity, id string, data domain.RequestObject, audit domain.LoginAuditContext) (domain.MessageTemplate, error) {
	tx, association, _, err := s.begin(ctx, identity, true)
	if err != nil {
		return domain.MessageTemplate{}, err
	}
	defer tx.Rollback()
	at := s.now()
	if err := seedMessageTemplates(ctx, tx, association, at); err != nil {
		return domain.MessageTemplate{}, err
	}
	current := domain.MessageTemplate{ID: uuid.NewString(), Enabled: true}
	if id != "" {
		rows, err := tx.Templates(ctx, association, false)
		if err != nil {
			return current, err
		}
		current, err = chooseMessageTemplate(rows, "", "", id)
		if err != nil {
			return current, err
		}
	}
	row, err := dto.ValidateMessageTemplate(data, current)
	if err != nil {
		return row, err
	}
	if _, err = dto.SampleMessageTemplate(row); err != nil {
		return row, err
	}
	audit = paymentCategoryAuditContext(identity, audit)
	if row.IsDefault {
		if err = tx.ClearDefault(ctx, association, row, audit, at); err != nil {
			return row, err
		}
	}
	if id == "" {
		err = tx.InsertTemplate(ctx, association, row, audit, at)
	} else {
		err = tx.UpdateTemplate(ctx, association, current, row, audit, at)
	}
	if err != nil {
		return row, err
	}
	return row, tx.Commit()
}

func (s *MessageTemplates) Archive(ctx context.Context, identity domain.ResolvedIdentity, id string, audit domain.LoginAuditContext) error {
	tx, association, _, err := s.begin(ctx, identity, true)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = tx.ArchiveTemplate(ctx, association, id, paymentCategoryAuditContext(identity, audit), s.now()); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *MessageTemplates) Sample(ctx context.Context, identity domain.ResolvedIdentity, data domain.RequestObject) (dto.MessageText, error) {
	tx, _, _, err := s.begin(ctx, identity, false)
	if err != nil {
		return dto.MessageText{}, err
	}
	defer tx.Rollback()
	row, err := dto.ValidateMessageTemplate(data, domain.MessageTemplate{Enabled: true})
	if err != nil {
		return dto.MessageText{}, err
	}
	return dto.SampleMessageTemplate(row)
}

func chooseMessageTemplate(rows []domain.MessageTemplate, channel, locale, id string) (domain.MessageTemplate, error) {
	if id != "" {
		for _, row := range rows {
			if row.ID == id && (channel == "" || row.Channel == channel) {
				return row, nil
			}
		}
	} else {
		language, _, _ := strings.Cut(locale, "-")
		for _, code := range []string{locale, language, "it"} {
			for _, row := range rows {
				if row.Channel == channel && row.Locale == code && row.IsDefault {
					return row, nil
				}
			}
		}
	}
	return domain.MessageTemplate{}, domain.ErrMessageTemplateNotFound
}

func (s *MessageTemplates) recipientTemplates(ctx context.Context, tx domain.MessageTemplateTransaction, association, recipientID, group string) (domain.MessageRecipient, []domain.MessageTemplate, error) {
	recipient, err := tx.Recipient(ctx, association, recipientID, supplierGroup(group))
	if err != nil {
		return recipient, nil, err
	}
	if err = seedMessageTemplates(ctx, tx, association, s.now()); err != nil {
		return recipient, nil, err
	}
	rows, err := tx.Templates(ctx, association, true)
	return recipient, rows, err
}

func (s *MessageTemplates) Options(ctx context.Context, identity domain.ResolvedIdentity, recipientID, group string) (OutgoingMessageOptions, error) {
	tx, association, _, err := s.begin(ctx, identity, true)
	if err != nil {
		return OutgoingMessageOptions{}, err
	}
	defer tx.Rollback()
	recipient, rows, err := s.recipientTemplates(ctx, tx, association, recipientID, group)
	if err != nil {
		return OutgoingMessageOptions{}, err
	}
	return OutgoingMessageOptions{RecipientName: dto.MessageRecipientName(recipient), Templates: rows}, tx.Commit()
}

func (s *MessageTemplates) Preview(ctx context.Context, identity domain.ResolvedIdentity, recipientID, group string, data domain.RequestObject) (OutgoingMessageResult, error) {
	tx, association, name, err := s.begin(ctx, identity, true)
	if err != nil {
		return OutgoingMessageResult{}, err
	}
	defer tx.Rollback()
	recipient, rows, err := s.recipientTemplates(ctx, tx, association, recipientID, group)
	if err != nil {
		return OutgoingMessageResult{}, err
	}
	channel, err := dto.MessageTemplateChannel(data)
	if err != nil {
		return OutgoingMessageResult{}, err
	}
	locale, err := dto.MessageTemplateLocale(data)
	if err != nil {
		return OutgoingMessageResult{}, err
	}
	var id string
	if value, _ := data.Lookup("template_id"); value != nil && value != "" {
		id, err = dto.MessageTemplateID(value)
		if err != nil {
			return OutgoingMessageResult{}, err
		}
	}
	template, err := chooseMessageTemplate(rows, channel, locale, id)
	if err != nil {
		return OutgoingMessageResult{}, dto.InvalidMessageTemplate("template_id", "Nessun modello attivo per il canale e la lingua.")
	}
	address := dto.MessageRecipientAddress(recipient, channel)
	if address == "" {
		return OutgoingMessageResult{}, dto.InvalidMessageTemplate("recipient", "Il destinatario non ha un recapito valido per questo canale.")
	}
	variables, err := dto.MessageTemplateContext(data)
	if err != nil {
		return OutgoingMessageResult{}, err
	}
	variables["customer_name"], variables["association_name"] = dto.MessageRecipientName(recipient), name
	message, err := dto.RenderMessageTemplate(template, variables)
	if err != nil {
		return OutgoingMessageResult{}, err
	}
	row := domain.OutgoingMessage{ID: uuid.NewString(), RecipientID: recipientID, TemplateID: template.ID, Channel: channel, Locale: template.Locale,
		Recipient: address, Subject: message.Subject, Body: message.Body, Status: "preview"}
	if err = tx.InsertOutgoingMessage(ctx, association, identity.Actor.ID, row, s.now()); err != nil {
		return OutgoingMessageResult{}, err
	}
	return s.result(row), tx.Commit()
}

func (s *MessageTemplates) Action(ctx context.Context, identity domain.ResolvedIdentity, id, group string, data domain.RequestObject, audit domain.LoginAuditContext) (OutgoingMessageResult, error) {
	row, association, claimed, err := s.claim(ctx, identity, id, group, data)
	if err != nil {
		return OutgoingMessageResult{}, err
	}
	if claimed && row.Status == "sending" {
		// The persisted claim survives a disconnected client or an uncertain provider response.
		work := context.WithoutCancel(ctx)
		accepted, err := s.delivery[row.Channel](work, association, row, paymentCategoryAuditContext(identity, audit))
		if err != nil {
			slog.Error("Unable to submit outgoing message", "message_id", row.ID)
		}
		row.Status = "failed"
		if accepted && err == nil {
			row.Status = "submitted"
		}
		finish, cancel := context.WithTimeout(work, 10*time.Second)
		defer cancel()
		if err := s.store.FinishOutgoingMessage(finish, row.ID, association, row.Status, s.now()); err != nil {
			return OutgoingMessageResult{}, err
		}
	}
	return s.result(row), nil
}

func (s *MessageTemplates) claim(ctx context.Context, identity domain.ResolvedIdentity, id, group string, data domain.RequestObject) (row domain.OutgoingMessage, association string, claimed bool, err error) {
	tx, association, _, err := s.begin(ctx, identity, false)
	if err != nil {
		return row, association, false, err
	}
	defer tx.Rollback()
	row, err = tx.LockOutgoingMessage(ctx, association, identity.Actor.ID, id)
	if err != nil {
		return row, association, false, err
	}
	value, _ := data.Lookup("action")
	action, _ := value.(string)
	if action != "send" && action != "discard" && action != "open_whatsapp" {
		return row, association, false, dto.InvalidMessageTemplate("action", "Azione non valida.")
	}
	edited, err := dto.OutgoingMessageText(data, row.Channel, action != "discard")
	if err != nil {
		return row, association, false, err
	}
	if action == "discard" {
		if _, provided := data.Lookup("subject"); !provided {
			edited.Subject = row.Subject
		}
		if _, provided := data.Lookup("body"); !provided {
			edited.Body = row.Body
		}
	}
	now := s.now()
	if row.Status != "preview" {
		if row.Action != action || row.Subject != edited.Subject || row.Body != edited.Body {
			return row, association, false, ErrOutgoingMessageUsed
		}
		if row.Status != "sending" || now.Before(row.UpdatedAt.Add(sendConfirmationWindow)) {
			return row, association, false, nil
		}
		// Keep the claim when the provider may already have accepted the message.
		row.Status = "unknown"
		if err := tx.SaveOutgoingMessageAction(ctx, association, row, now); err != nil {
			return row, association, false, err
		}
		return row, association, false, tx.Commit()
	}
	status, err := s.actionStatus(ctx, tx, association, group, row, action)
	if err != nil {
		return row, association, false, err
	}
	row.Action, row.Status, row.Subject, row.Body = action, status, edited.Subject, edited.Body
	if err := tx.SaveOutgoingMessageAction(ctx, association, row, now); err != nil {
		return row, association, false, err
	}
	return row, association, true, tx.Commit()
}

func (s *MessageTemplates) actionStatus(ctx context.Context, tx domain.MessageTemplateTransaction, association, group string, row domain.OutgoingMessage, action string) (string, error) {
	if action == "discard" {
		return "discarded", nil
	}
	recipient, err := tx.Recipient(ctx, association, row.RecipientID, supplierGroup(group))
	if err != nil && !errors.Is(err, domain.ErrMessageTemplateNotFound) {
		return "", err
	}
	if err != nil || dto.MessageRecipientAddress(recipient, row.Channel) != row.Recipient {
		return "", ErrOutgoingRecipientChanged
	}
	direct := s.delivery[row.Channel] != nil
	if action == "open_whatsapp" {
		if row.Channel != "whatsapp" || direct {
			return "", dto.InvalidMessageTemplate("action", "Apertura manuale di WhatsApp non disponibile.")
		}
		return "manual", nil
	}
	if direct {
		return "sending", nil
	}
	return "skipped", nil
}
