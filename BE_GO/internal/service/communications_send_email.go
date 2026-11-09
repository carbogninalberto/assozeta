package service

import (
	"context"
	"errors"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/smtpsend"
	"github.com/google/uuid"
)

// CommunicationSendEmailSettings are core.settings' DEFAULT_FROM_EMAIL and WHITELABEL_NAME.
type CommunicationSendEmailSettings struct{ DefaultFromEmail, WhitelabelName string }

// CommunicationSendEmailSMTP is the smtplib side of send_email: verify_smtp
// (connect, STARTTLS, login; closed without QUIT) and the delivery connection.
type CommunicationSendEmailSMTP interface {
	Verify(context.Context, smtpsend.Server) error
	Send(ctx context.Context, server smtpsend.Server, from string, to []string, message string) error
}

type communicationSendEmailSMTP struct{}

func (communicationSendEmailSMTP) Verify(ctx context.Context, server smtpsend.Server) error {
	return smtpsend.Verify(ctx, server)
}

func (communicationSendEmailSMTP) Send(ctx context.Context, server smtpsend.Server, from string, to []string, message string) error {
	return smtpsend.Send(ctx, server, from, to, message)
}

// CommunicationSendEmail ports configuration_send_email.
type CommunicationSendEmail struct {
	store     domain.CommunicationSendEmailStore
	publisher celery.Publisher
	smtp      CommunicationSendEmailSMTP
	settings  CommunicationSendEmailSettings
	now       func() time.Time
	boundary  func() string
}

func NewCommunicationSendEmail(store domain.CommunicationSendEmailStore, publisher celery.Publisher, settings CommunicationSendEmailSettings,
	now func() time.Time) *CommunicationSendEmail {
	return &CommunicationSendEmail{store: store, publisher: publisher, smtp: communicationSendEmailSMTP{}, settings: settings, now: now,
		boundary: smtpsend.NewBoundary}
}

// SendEmailResult is the view's {'msg': msg, 'result': result}.
type SendEmailResult struct {
	Msg    string `json:"msg"`
	Result bool   `json:"result"`
}

// step runs fn in its own transaction (each Django create and save autocommits).
func (s *CommunicationSendEmail) step(ctx context.Context, fn func(domain.CommunicationSendEmailTransaction) error) error {
	tx, err := s.store.BeginCommunicationSendEmail(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = fn(tx); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *CommunicationSendEmail) stamp() time.Time { return s.now().UTC().Truncate(time.Microsecond) }

// Send follows the view: request.user.sport_association, the optional stored
// message, EmailSerializer, the Message and its MessageTransactions, then
// CommunicationConfiguration.send_email(subject, body, recipient_list).
func (s *CommunicationSendEmail) Send(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool,
	audit domain.LoginAuditContext) (SendEmailResult, error) {
	owner, err := emailLogsOwner(identity)
	if err != nil {
		return SendEmailResult{}, err
	}
	var association string
	var stored *domain.SendEmailMessage
	var data domain.RequestObject
	switch typed := body.(type) {
	case nil:
		data = domain.RequestObject{}
	case domain.RequestObject:
		data = typed
	}
	err = s.step(ctx, func(tx domain.CommunicationSendEmailTransaction) error {
		var err error
		if association, err = tx.Association(ctx, owner, false); err != nil {
			return err
		}
		if body != nil && data == nil {
			return domain.ErrSendEmailServerError // `'message_id' in data` / data['message_id'] on a non-dict
		}
		id, present := data.Lookup("message_id")
		if !present || !dto.PythonTruthy(id) {
			return nil
		}
		key, ok := dto.PersonaUUIDValue(id)
		if !ok {
			return domain.ErrSendEmailServerError
		}
		if stored, err = tx.StoredMessage(ctx, key, association); err != nil {
			return err
		}
		if stored == nil {
			return domain.ErrSendEmailMessageNotFound
		}
		if form {
			return domain.ErrSendEmailServerError // the QueryDict is immutable
		}
		return nil
	})
	if err != nil {
		return SendEmailResult{}, err
	}
	if stored != nil {
		// data['message'] = message.message; data['subject'] = message.subject
		replaced := domain.RequestObject{}
		for _, field := range data {
			if field.Name != "message" && field.Name != "subject" {
				replaced = append(replaced, field)
			}
		}
		var subject any
		if stored.Subject != nil {
			subject = *stored.Subject
		}
		data = append(replaced, domain.RequestField{Name: "message", Value: stored.Message}, domain.RequestField{Name: "subject", Value: subject})
	}
	input, err := dto.ValidateSendEmail(data)
	if err != nil {
		return SendEmailResult{}, err
	}
	recipients := strings.Split(input.Email, ",")
	if err = s.refuseUnsupported(ctx, association, input, recipients); err != nil {
		return SendEmailResult{}, err
	}
	auditContext := paymentCategoryAuditContext(identity, audit)
	messageID := ""
	if stored != nil {
		messageID = stored.ID
	} else {
		messageID = uuid.NewString()
		subject := input.Subject
		if err = s.step(ctx, func(tx domain.CommunicationSendEmailTransaction) error {
			return tx.InsertMessage(ctx, domain.SendEmailMessage{ID: messageID, AssociationID: association, Message: input.Message,
				Subject: &subject}, s.stamp(), auditContext)
		}); err != nil {
			return SendEmailResult{}, err
		}
	}
	for _, recipient := range recipients {
		if err = s.step(ctx, func(tx domain.CommunicationSendEmailTransaction) error {
			return tx.InsertMessageTransaction(ctx, uuid.NewString(), messageID, recipient, s.stamp())
		}); err != nil {
			return SendEmailResult{}, err
		}
	}
	var configuration domain.CommunicationConfigurationRecord
	if err = s.step(ctx, func(tx domain.CommunicationSendEmailTransaction) error {
		var err error
		configuration, err = tx.FirstConfiguration(ctx, association, false)
		return err
	}); err != nil {
		if errors.Is(err, domain.ErrCommunicationConfigurationNotFound) {
			return SendEmailResult{}, domain.ErrSendEmailServerError // None.send_email
		}
		return SendEmailResult{}, err
	}
	return s.sendEmail(ctx, configuration, input, recipients, auditContext)
}

func sendEmailConfigured(row domain.CommunicationConfigurationRecord) bool {
	return row.Host != nil && *row.Host != "" && row.Port != nil && *row.Port != 0 && row.User != nil && *row.User != "" &&
		row.Password != nil && *row.Password != ""
}

func sendEmailServer(row domain.CommunicationConfigurationRecord) smtpsend.Server {
	return smtpsend.Server{Host: *row.Host, Port: int(*row.Port), User: *row.User, Password: *row.Password, Encryption: row.Encryption,
		Timeout: 10 * time.Second}
}

func (s *CommunicationSendEmail) render(row domain.CommunicationConfigurationRecord, input dto.SendEmailInput, recipients []string) (string, error) {
	sender := ""
	if row.SenderName != nil {
		sender = *row.SenderName
	}
	message, err := smtpsend.Render(smtpsend.Message{SenderName: sender, SenderUser: *row.User, To: recipients, Subject: input.Subject,
		Text: input.Message}, s.boundary())
	if err == nil {
		err = smtpsend.CheckEnvelope(*row.User, recipients)
	}
	if err == nil {
		for i := 0; i < len(message); i++ {
			if message[i] >= 0x80 {
				return "", smtpsend.ErrUnsupported
			}
		}
	}
	return message, err
}

// refuseUnsupported reads the configuration before any write: when it would
// take the SMTP path, a message or envelope outside smtpsend's subset is
// refused (Django would send it).
func (s *CommunicationSendEmail) refuseUnsupported(ctx context.Context, association string, input dto.SendEmailInput, recipients []string) error {
	return s.step(ctx, func(tx domain.CommunicationSendEmailTransaction) error {
		row, err := tx.FirstConfiguration(ctx, association, false)
		if errors.Is(err, domain.ErrCommunicationConfigurationNotFound) {
			return nil
		}
		if err != nil || row.DailyBalance >= row.DailyLimit || !sendEmailConfigured(row) {
			return err
		}
		if _, err = s.render(row, input, recipients); errors.Is(err, smtpsend.ErrUnsupported) {
			return errors.Join(domain.ErrSendEmailNotPorted, err)
		}
		return err
	})
}

// sendEmail is CommunicationConfiguration.send_email(subject, body, recipient_list).
func (s *CommunicationSendEmail) sendEmail(ctx context.Context, row domain.CommunicationConfigurationRecord, input dto.SendEmailInput,
	recipients []string, audit domain.LoginAuditContext) (SendEmailResult, error) {
	if row.DailyBalance >= row.DailyLimit {
		return SendEmailResult{Msg: "Daily email limit reached", Result: false}, nil
	}
	// The SMTP conversation is not interrupted when the client goes away.
	work := context.WithoutCancel(ctx)
	if sendEmailConfigured(row) {
		server := sendEmailServer(row)
		if err := s.smtp.Verify(work, server); err == nil {
			message, err := s.render(row, input, recipients)
			if err != nil {
				return SendEmailResult{}, errors.Join(domain.ErrSendEmailNotPorted, err)
			}
			if err = s.smtp.Send(work, server, *row.User, recipients, message); err != nil {
				// send_email returns (False, exception); DRF cannot render it.
				return SendEmailResult{}, errors.Join(domain.ErrSendEmailServerError, err)
			}
			return SendEmailResult{Msg: "Ok", Result: true}, s.saveBalance(work, row, audit)
		}
	}
	var denomination *string
	if err := s.step(work, func(tx domain.CommunicationSendEmailTransaction) error {
		var err error
		denomination, err = tx.Denomination(work, *row.AssociationID)
		return err
	}); err != nil {
		return SendEmailResult{}, err
	}
	if denomination == nil {
		return SendEmailResult{}, domain.ErrSendEmailServerError // str.replace(..., None)
	}
	list := make([]any, len(recipients))
	for i, recipient := range recipients {
		list[i] = recipient
	}
	publishCtx, cancel := context.WithTimeout(work, 30*time.Second)
	defer cancel()
	if _, err := s.publisher.Publish(publishCtx, celery.Task{Name: "core.tasks.send_mail_async", Kwargs: celery.Dict{
		{Key: "subject", Value: input.Subject}, {Key: "message", Value: input.Message},
		{Key: "from_email", Value: strings.ReplaceAll(s.settings.DefaultFromEmail, s.settings.WhitelabelName, *denomination)},
		{Key: "recipient_list", Value: list}, {Key: "fail_silently", Value: false},
		{Key: "sport_association_id", Value: uuid.MustParse(*row.AssociationID)}, {Key: "reply_to", Value: nil},
		{Key: "skip_association_check", Value: true},
	}}); err != nil {
		return SendEmailResult{}, errors.Join(domain.ErrSendEmailServerError, err)
	}
	return SendEmailResult{Msg: "Ok", Result: true}, nil
}

// saveBalance is daily_email_balance += 1 and save() of the loaded instance:
// every column written, auditlog diffing the stored row (password excluded).
func (s *CommunicationSendEmail) saveBalance(ctx context.Context, loaded domain.CommunicationConfigurationRecord, audit domain.LoginAuditContext) error {
	return s.step(ctx, func(tx domain.CommunicationSendEmailTransaction) error {
		old, err := tx.ConfigurationByID(ctx, loaded.ID)
		if err != nil {
			return err
		}
		row := loaded
		row.DailyBalance++
		if err = tx.UpdateConfiguration(ctx, row); err != nil {
			return err
		}
		changes := map[string][2]string{}
		previous := smtpAuditFields(old)
		for index, field := range smtpAuditFields(row) {
			if before, after := smtpAuditText(previous[index][1]), smtpAuditText(field[1]); before != after {
				changes[field[0].(string)] = [2]string{before, after}
			}
		}
		if row.AssociationID != nil && !(old.AssociationID != nil && *old.AssociationID == *row.AssociationID) {
			changes["sport_association"] = [2]string{smtpAuditText(old.AssociationID), smtpAuditText(row.AssociationID)}
		}
		if len(changes) == 0 {
			return nil
		}
		return tx.AppendConfigurationAudit(ctx, domain.CommunicationConfigurationAudit{ID: row.ID, AssociationID: *row.AssociationID,
			Action: 1, Changes: changes, Context: audit, At: s.stamp()})
	})
}
