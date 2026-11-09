package service

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"math/rand/v2"
	"strings"
	"sync"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/emailrender"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/smtpsend"
	"github.com/google/uuid"
)

// EmailTaskSettings are the core.settings values the e-mail tasks read.
type EmailTaskSettings struct {
	AppURL, WhitelabelName, DefaultFromEmail, DefaultTeamEmail string
	IsWhitelabel                                               bool
}

// EmailTasks implements send_user_partial_registration_email,
// send_expiring_certificate_email, send_expired_certificate_email and
// send_reminders. Each task catches every exception and returns None; Go
// records that handled outcome as a NULL completion with the earlier
// committed effects kept.
type EmailTasks struct {
	store     domain.EmailTaskStore
	publisher celery.Publisher
	mailer    domain.SMTPMailer
	settings  EmailTaskSettings
	now       func() time.Time
	delay     func() time.Duration
	logger    *slog.Logger
	timers    sync.WaitGroup
}

func NewEmailTasks(store domain.EmailTaskStore, publisher celery.Publisher, mailer domain.SMTPMailer, settings EmailTaskSettings,
	now func() time.Time, logger *slog.Logger) *EmailTasks {
	return &EmailTasks{store: store, publisher: publisher, mailer: mailer, settings: settings, now: now, logger: logger,
		// send_email_html: random.randint(1, 30) seconds.
		delay: func() time.Duration { return time.Duration(1+rand.IntN(30)) * time.Second }}
}

// errEmailTaskStopped is an exception the task's outer handler catches.
var errEmailTaskStopped = errors.New("email task stopped")

func emailTaskEnvelope(job domain.Job, kind string) error {
	var payload map[string]json.RawMessage
	if job.AssociationID != uuid.Nil || job.ID == uuid.Nil || job.LeaseToken == uuid.Nil || job.State != "running" || job.Kind != kind ||
		job.Version != 1 || len(job.Payload) > 65536 || json.Unmarshal(job.Payload, &payload) != nil || payload == nil || len(payload) != 0 {
		return domain.ErrJobInvalid
	}
	return nil
}

// finish completes the job with a NULL result after a normal end or a
// handled stop; other errors go back to the executor.
func (s *EmailTasks) finish(ctx context.Context, job domain.Job, err error, name string) error {
	if err != nil && !errors.Is(err, errEmailTaskStopped) {
		return err
	}
	if err != nil {
		s.logger.WarnContext(ctx, name+" stopped", "code", name+"_stopped")
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	completion, err := s.store.BeginEmailTask(ctx, &job)
	if err != nil {
		return err
	}
	defer completion.Rollback()
	if err = completion.CompleteJob(ctx, job.ID, job.LeaseToken); err != nil {
		return err
	}
	return completion.Commit()
}

// Wait blocks until the delayed reminder sends started by send_reminders end,
// as the worker process waits for the task's non-daemon Timer threads.
func (s *EmailTasks) Wait() { s.timers.Wait() }

func (s *EmailTasks) render(ctx context.Context, tx domain.EmailTaskTransaction, name string, context map[string]any) (string, error) {
	stored, err := tx.InstanceConfig(ctx)
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
		return "", errors.Join(domain.ErrEmailTaskUnsupported, err)
	}
	// No language is activated in the worker: LANGUAGE_CODE.
	html, err := emailrender.Render(name, context, emailrender.Options{Language: "it", Brand: brand})
	if err != nil {
		return "", errors.Join(domain.ErrEmailTaskUnsupported, err)
	}
	return html, nil
}

func (s *EmailTasks) brandSettings() map[string]any {
	return map[string]any{"WHITELABEL_NAME": s.settings.WhitelabelName, "IS_WHITELABEL": s.settings.IsWhitelabel}
}

func utcDate(value time.Time) time.Time {
	value = value.UTC()
	return time.Date(value.Year(), value.Month(), value.Day(), 0, 0, 0, 0, time.UTC)
}

// publishError maps a broker failure: cancellation is the executor's, any
// other failure is the exception the Django code raised.
func publishError(ctx context.Context, err error) error {
	if ctx.Err() != nil {
		return ctx.Err()
	}
	return errors.Join(errEmailTaskStopped, err)
}

// --- send_user_partial_registration_email ------------------------------------

// RunPartialRegistrations: registrations created since yesterday's UTC
// midnight; for each, the rendered invitation is published to send_mail_async
// and then the row is deleted (audited), each pair committed on its own.
func (s *EmailTasks) RunPartialRegistrations(ctx context.Context, job domain.Job) error {
	if err := emailTaskEnvelope(job, domain.JobSendUserPartialRegistrationEmail); err != nil {
		return err
	}
	reader, err := s.store.BeginEmailTask(ctx, &job)
	if err != nil {
		return err
	}
	defer reader.Rollback()
	partials, err := reader.PartialRegistrations(ctx, utcDate(s.now()).AddDate(0, 0, -1))
	if err != nil {
		return err
	}
	messages := make([]string, len(partials))
	for i, partial := range partials {
		// Rendering is pure for a given instance configuration: an input the
		// renderer refuses ends the run before any effect.
		if messages[i], err = s.render(ctx, reader, emailrender.PartialRegistration, map[string]any{
			"email": partial.Email, "app_host": s.settings.AppURL, "settings": s.brandSettings()}); err != nil {
			return err
		}
	}
	if err = reader.Rollback(); err != nil {
		return err
	}
	for i, partial := range partials {
		if err = s.partialRegistration(ctx, job, partial, messages[i]); err != nil {
			break
		}
	}
	return s.finish(ctx, job, err, "user_partial_registration")
}

func (s *EmailTasks) partialRegistration(ctx context.Context, job domain.Job, partial domain.UserPartialRegistration, message string) error {
	tx, err := s.store.BeginEmailTask(ctx, &job)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = s.publisher.Publish(ctx, celery.Task{Name: "send_mail_async", Kwargs: celery.Dict{
		{Key: "subject", Value: "Completa la tua registrazione per attivare la prova gratuita di " + s.settings.WhitelabelName},
		{Key: "message", Value: message}, {Key: "from_email", Value: s.settings.DefaultTeamEmail},
		{Key: "recipient_list", Value: []any{pythonLower(partial.Email)}}, {Key: "html_message", Value: message},
		{Key: "fail_silently", Value: false},
	}}); err != nil {
		return publishError(ctx, err)
	}
	if err = tx.DeletePartialRegistration(ctx, partial, s.now().UTC().Truncate(time.Microsecond)); err != nil {
		return err
	}
	return tx.Commit()
}

// --- send_expiring_certificate_email / send_expired_certificate_email ----------

type certificateTask struct {
	kind, template, subject string
	expirations             func(today time.Time) []time.Time
}

var (
	expiringCertificates = certificateTask{kind: domain.JobSendExpiringCertificateEmail, template: emailrender.CertificateExpiring,
		subject: " | Scadenza certificato medico", expirations: func(today time.Time) []time.Time {
			return []time.Time{today.AddDate(0, 0, 7), today.AddDate(0, 0, 30)}
		}}
	expiredCertificates = certificateTask{kind: domain.JobSendExpiredCertificateEmail, template: emailrender.CertificateExpired,
		subject: " | Certificato medico scaduto", expirations: func(today time.Time) []time.Time {
			return []time.Time{today.AddDate(0, 0, -1)}
		}}
)

func (s *EmailTasks) RunExpiringCertificates(ctx context.Context, job domain.Job) error {
	return s.runCertificates(ctx, job, expiringCertificates)
}

func (s *EmailTasks) RunExpiredCertificates(ctx context.Context, job domain.Job) error {
	return s.runCertificates(ctx, job, expiredCertificates)
}

// runCertificates follows the tasks: the queryset is read and filtered by
// is_current first; then for each subscription whose owner keeps
// notifications, the recipient (associate, main tutor, user), the render and
// either the association's CommunicationConfiguration.send_email or a
// send_mail_async publication. A planning pass validates every SMTP message
// the run could send before the first effect.
func (s *EmailTasks) runCertificates(ctx context.Context, job domain.Job, task certificateTask) error {
	if err := emailTaskEnvelope(job, task.kind); err != nil {
		return err
	}
	now := s.now().UTC()
	reader, err := s.store.BeginEmailTask(ctx, &job)
	if err != nil {
		return err
	}
	defer reader.Rollback()
	all, err := reader.CertificateSubscriptions(ctx, now.Add(-366*24*time.Hour), task.expirations(utcDate(now)))
	if err != nil {
		return err
	}
	var current []domain.CertificateSubscription
	for _, subscription := range all {
		today := utcDate(s.now())
		if subscription.StartDate == nil || subscription.EndDate == nil ||
			(!utcDate(*subscription.StartDate).After(today) && !today.After(utcDate(*subscription.EndDate))) {
			current = append(current, subscription)
		}
	}
	for _, subscription := range current {
		send, err := s.certificateMessage(ctx, nil, reader, task, subscription)
		if errors.Is(err, errEmailTaskStopped) {
			break
		}
		if err != nil {
			return err
		}
		if send != nil && send.configuration != nil {
			if err = s.checkConfigurationSend(*send.configuration, send.subject, []*string{&send.recipient}, []string{subscription.OwnerEmail}); err != nil {
				return err
			}
		}
	}
	if err = reader.Rollback(); err != nil {
		return err
	}
	for _, subscription := range current {
		if err = s.certificate(ctx, job, task, subscription); err != nil {
			break
		}
	}
	return s.finish(ctx, job, err, task.kind)
}

type certificateSend struct {
	recipient, subject, message string
	configuration               *domain.CommunicationConfiguration
}

// certificateMessage resolves the recipient and renders the notice. With a
// job, get_main_tutor's save of the first relation is committed; without one
// (planning) nothing is written. A nil result means the subscription is skipped.
func (s *EmailTasks) certificateMessage(ctx context.Context, job *domain.Job, tx domain.EmailTaskTransaction, task certificateTask,
	subscription domain.CertificateSubscription) (*certificateSend, error) {
	if !subscription.OwnerNotifications {
		return nil, nil
	}
	if subscription.AssociateID == nil {
		return nil, errEmailTaskStopped // subscription.associate is None
	}
	var athlete *string
	if email := subscription.AssociateEmail; email != nil && dto.SupplierEmail(*email) {
		athlete = email
	}
	relation, err := tx.FirstTutorRelation(ctx, *subscription.AssociateID, true)
	if err != nil {
		return nil, err
	}
	if relation == nil {
		if relation, err = tx.FirstTutorRelation(ctx, *subscription.AssociateID, false); err != nil {
			return nil, err
		}
		if relation != nil && job != nil {
			writer, err := s.store.BeginEmailTask(ctx, job)
			if err != nil {
				return nil, err
			}
			defer writer.Rollback()
			if err = writer.MarkTutorRelationPrimary(ctx, *relation, s.now().UTC().Truncate(time.Microsecond)); err != nil {
				return nil, err
			}
			if err = writer.Commit(); err != nil {
				return nil, err
			}
		}
	}
	if athlete == nil && relation != nil {
		email, err := tx.AssociateEmail(ctx, relation.Tutor)
		if err != nil {
			return nil, err
		}
		if email != nil && dto.SupplierEmail(*email) {
			athlete = email
		}
	}
	recipient := subscription.UserEmail
	if athlete != nil {
		recipient = *athlete
	}
	message, err := s.render(ctx, tx, task.template, map[string]any{
		"athlete_first_name": optionalValue(subscription.AssociateFirstName), "athlete_last_name": optionalValue(subscription.AssociateLastName),
		"sport_association":         map[string]any{"denomination": optionalValue(subscription.Denomination)},
		"certificate_expiring_date": subscription.CertificateExpiration.Format("02/01/2006"),
		"app_host":                  s.settings.AppURL, "settings": s.brandSettings(),
	})
	if err != nil {
		return nil, err
	}
	configuration, err := tx.FirstCommunicationConfiguration(ctx, subscription.AssociationID)
	if err != nil {
		return nil, err
	}
	return &certificateSend{recipient: pythonLower(recipient), subject: s.settings.WhitelabelName + task.subject, message: message,
		configuration: configuration}, nil
}

func (s *EmailTasks) certificate(ctx context.Context, job domain.Job, task certificateTask, subscription domain.CertificateSubscription) error {
	reader, err := s.store.BeginEmailTask(ctx, &job)
	if err != nil {
		return err
	}
	defer reader.Rollback()
	send, err := s.certificateMessage(ctx, &job, reader, task, subscription)
	if err != nil || send == nil {
		return err
	}
	if err = reader.Rollback(); err != nil {
		return err
	}
	replyTo := []string{subscription.OwnerEmail}
	if send.configuration != nil {
		return s.configurationSend(ctx, &job, *send.configuration, send.subject, send.message, []*string{&send.recipient}, replyTo)
	}
	if _, err = s.publisher.Publish(ctx, celery.Task{Name: "send_mail_async", Kwargs: celery.Dict{
		{Key: "subject", Value: send.subject}, {Key: "message", Value: send.message}, {Key: "from_email", Value: s.settings.DefaultTeamEmail},
		{Key: "recipient_list", Value: []any{send.recipient}}, {Key: "html_message", Value: send.message},
		{Key: "fail_silently", Value: false}, {Key: "sport_association_id", Value: subscription.AssociationID},
		{Key: "reply_to", Value: []any{subscription.OwnerEmail}},
	}}); err != nil {
		return publishError(ctx, err)
	}
	return nil
}

// --- CommunicationConfiguration.send_email -----------------------------------

func smtpConfigured(configuration domain.CommunicationConfiguration) bool {
	return configuration.Host != nil && *configuration.Host != "" && configuration.Port != nil && *configuration.Port != 0 &&
		configuration.User != nil && *configuration.User != "" && configuration.Password != nil && *configuration.Password != ""
}

func smtpServer(configuration domain.CommunicationConfiguration) domain.SMTPServer {
	return domain.SMTPServer{Host: *configuration.Host, Port: int(*configuration.Port), User: *configuration.User,
		Password: *configuration.Password, Encryption: configuration.Encryption}
}

func smtpMessage(configuration domain.CommunicationConfiguration, subject, html string, recipients []string, replyTo []string) domain.SMTPMessage {
	sender := ""
	if configuration.SenderName != nil {
		sender = *configuration.SenderName
	}
	return domain.SMTPMessage{SenderName: sender, SenderUser: *configuration.User, To: recipients, Subject: subject, ReplyTo: replyTo, HTML: html}
}

func presentRecipients(recipients []*string) ([]string, bool) {
	out := make([]string, len(recipients))
	for i, recipient := range recipients {
		if recipient == nil {
			return nil, false // ', '.join raises on None
		}
		out[i] = *recipient
	}
	return out, true
}

// checkConfigurationSend reports an SMTP message the smtpsend subset refuses
// when the configuration would take the SMTP path.
func (s *EmailTasks) checkConfigurationSend(configuration domain.CommunicationConfiguration, subject string, recipients []*string, replyTo []string) error {
	if configuration.DailyBalance >= configuration.DailyLimit || !smtpConfigured(configuration) {
		return nil
	}
	to, ok := presentRecipients(recipients)
	if !ok {
		return nil
	}
	// The body is rendered HTML (valid UTF-8); only headers and envelope vary.
	return s.mailer.Check(smtpServer(configuration), smtpMessage(configuration, subject, "<p></p>", to, replyTo))
}

// configurationSend is CommunicationConfiguration.send_email(subject,
// body=None, html_body=html, ...) on the loaded instance: daily limit, then
// either verify_smtp and the SMTP delivery with the balance save, or
// send_mail_async under the association's name. Its own try/except returns
// False on any failure, so failures here end nothing.
func (s *EmailTasks) configurationSend(ctx context.Context, job *domain.Job, configuration domain.CommunicationConfiguration,
	subject, html string, recipients []*string, replyTo []string) error {
	if configuration.DailyBalance >= configuration.DailyLimit {
		return nil
	}
	if smtpConfigured(configuration) {
		server := smtpServer(configuration)
		if err := s.mailer.Verify(ctx, server); err == nil {
			to, ok := presentRecipients(recipients)
			if !ok {
				return nil
			}
			if err = s.mailer.Send(ctx, server, smtpMessage(configuration, subject, html, to, replyTo)); err != nil {
				if ctx.Err() != nil {
					return ctx.Err()
				}
				if errors.Is(err, domain.ErrEmailTaskUnsupported) {
					return err
				}
				s.logger.WarnContext(ctx, "communication smtp send failed", "code", "communication_smtp_failed")
				return nil
			}
			configuration.DailyBalance++
			writer, err := s.store.BeginEmailTask(ctx, job)
			if err != nil {
				return err
			}
			defer writer.Rollback()
			if err = writer.SaveCommunicationConfiguration(ctx, configuration, s.now().UTC().Truncate(time.Microsecond)); err != nil {
				return err
			}
			return writer.Commit()
		} else if ctx.Err() != nil {
			return ctx.Err()
		}
	}
	if configuration.AssociationID == nil {
		return nil // self.sport_association is None
	}
	reader, err := s.store.BeginEmailTask(ctx, job)
	if err != nil {
		return err
	}
	defer reader.Rollback()
	denomination, err := reader.AssociationDenomination(ctx, *configuration.AssociationID)
	if err != nil {
		return err
	}
	if err = reader.Rollback(); err != nil {
		return err
	}
	if denomination == nil {
		return nil // str.replace(..., None) raises TypeError
	}
	list := make([]any, len(recipients))
	for i, recipient := range recipients {
		list[i] = optionalValue(recipient)
	}
	reply := make([]any, len(replyTo))
	for i, address := range replyTo {
		reply[i] = address
	}
	if _, err = s.publisher.Publish(ctx, celery.Task{Name: "send_mail_async", Kwargs: celery.Dict{
		{Key: "subject", Value: subject}, {Key: "message", Value: nil},
		{Key: "from_email", Value: strings.ReplaceAll(s.settings.DefaultFromEmail, s.settings.WhitelabelName, *denomination)},
		{Key: "recipient_list", Value: list}, {Key: "fail_silently", Value: false}, {Key: "html_message", Value: html},
		{Key: "sport_association_id", Value: *configuration.AssociationID}, {Key: "reply_to", Value: reply},
		{Key: "skip_association_check", Value: true},
	}}); err != nil && ctx.Err() != nil {
		return ctx.Err()
	}
	return nil
}

// --- send_reminders -------------------------------------------------------------

// RunReminders: due, not completed reminders with an association; each is
// rendered once, sent to the user and the instructor through send_email_html
// (called in the task, not queued), then saved as completed.
func (s *EmailTasks) RunReminders(ctx context.Context, job domain.Job) error {
	if err := emailTaskEnvelope(job, domain.JobSendReminders); err != nil {
		return err
	}
	reader, err := s.store.BeginEmailTask(ctx, &job)
	if err != nil {
		return err
	}
	defer reader.Rollback()
	reminders, err := reader.DueReminders(ctx, s.now().UTC().Truncate(time.Microsecond))
	if err != nil {
		return err
	}
	messages := make([]string, len(reminders))
	for i, reminder := range reminders {
		description := "Nessun dettaglio."
		if reminder.Description != nil && *reminder.Description != "" {
			description = *reminder.Description
		}
		if messages[i], err = s.render(ctx, reader, emailrender.ReminderEvent, map[string]any{
			"event_title": reminder.Title, "event_reminder_text": optionalValue(reminder.ReminderText),
			"event_description": description, "association_name": optionalValue(reminder.Denomination),
			"application_name": s.settings.WhitelabelName}); err != nil {
			return err
		}
		configuration, err := reader.FirstCommunicationConfiguration(ctx, reminder.AssociationID)
		if err != nil {
			return err
		}
		if configuration == nil {
			continue
		}
		for _, recipients := range reminderRecipients(reminder) {
			if err = s.checkConfigurationSend(*configuration, s.reminderSubject(reminder), recipients, []string{reminder.OwnerEmail}); err != nil {
				return err
			}
		}
	}
	if err = reader.Rollback(); err != nil {
		return err
	}
	for i, reminder := range reminders {
		if err = s.reminder(ctx, job, reminder, messages[i]); err != nil {
			break
		}
	}
	return s.finish(ctx, job, err, "send_reminders")
}

func reminderRecipients(reminder domain.EventReminder) [][]*string {
	user := reminder.UserEmail
	recipients := [][]*string{{&user}}
	if reminder.HasInstructor {
		recipients = append(recipients, []*string{reminder.InstructorEmail})
	}
	return recipients
}

func (s *EmailTasks) reminderSubject(reminder domain.EventReminder) string {
	return s.settings.WhitelabelName + " | Promemoria evento " + reminder.Title
}

func (s *EmailTasks) reminder(ctx context.Context, job domain.Job, reminder domain.EventReminder, message string) error {
	replyTo := []string{reminder.OwnerEmail}
	for _, recipients := range reminderRecipients(reminder) {
		if err := s.sendEmailHTML(ctx, job, reminder, message, recipients, replyTo); err != nil {
			return err
		}
	}
	writer, err := s.store.BeginEmailTask(ctx, &job)
	if err != nil {
		return err
	}
	defer writer.Rollback()
	if err = writer.CompleteReminder(ctx, reminder, s.now().UTC().Truncate(time.Microsecond)); err != nil {
		return err
	}
	return writer.Commit()
}

// sendEmailHTML is send_reminders' send_email_html call.
func (s *EmailTasks) sendEmailHTML(ctx context.Context, job domain.Job, reminder domain.EventReminder, message string, recipients []*string, replyTo []string) error {
	return s.SendEmailHTML(ctx, job, reminder.AssociationID, s.reminderSubject(reminder), message, recipients, replyTo)
}

// SendEmailHTML is send_email_html(html_message, subject, recipient_list,
// sport_association_id, reply_to) called in-process: with a configuration
// the send runs after a random 1-30 s delay outside the task (Timer); without
// one, send_mail_async is published. Its own try/except swallows failures.
func (s *EmailTasks) SendEmailHTML(ctx context.Context, job domain.Job, association uuid.UUID, subject, message string, recipients []*string,
	replyTo []string) error {
	reader, err := s.store.BeginEmailTask(ctx, &job)
	if err != nil {
		return err
	}
	defer reader.Rollback()
	configuration, err := reader.FirstCommunicationConfiguration(ctx, association)
	if err != nil {
		return err
	}
	if err = reader.Rollback(); err != nil {
		return err
	}
	if configuration != nil {
		loaded := *configuration
		s.timers.Add(1)
		time.AfterFunc(s.delay(), func() {
			defer s.timers.Done()
			timerCtx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
			defer cancel()
			if err := s.configurationSend(timerCtx, nil, loaded, subject, message, recipients, replyTo); err != nil {
				s.logger.Warn("delayed e-mail send failed", "code", "send_email_html_delayed_failed")
			}
		})
		return nil
	}
	list := make([]any, len(recipients))
	for i, recipient := range recipients {
		list[i] = optionalValue(recipient)
	}
	reply := make([]any, len(replyTo))
	for i, address := range replyTo {
		reply[i] = address
	}
	if _, err = s.publisher.Publish(ctx, celery.Task{Name: "send_mail_async", Kwargs: celery.Dict{
		{Key: "subject", Value: subject}, {Key: "message", Value: message}, {Key: "from_email", Value: s.settings.DefaultFromEmail},
		{Key: "recipient_list", Value: list}, {Key: "html_message", Value: message}, {Key: "fail_silently", Value: false},
		{Key: "sport_association_id", Value: association}, {Key: "reply_to", Value: reply},
	}}); err != nil && ctx.Err() != nil {
		return ctx.Err()
	}
	return nil
}

// CheckEmailHTML reports, before any effect, a send_email_html call whose
// SMTP path would need a message outside the smtpsend subset.
func (s *EmailTasks) CheckEmailHTML(ctx context.Context, job domain.Job, association uuid.UUID, subject string, recipients []*string,
	replyTo []string) error {
	reader, err := s.store.BeginEmailTask(ctx, &job)
	if err != nil {
		return err
	}
	defer reader.Rollback()
	configuration, err := reader.FirstCommunicationConfiguration(ctx, association)
	if err != nil || configuration == nil {
		return err
	}
	return s.checkConfigurationSend(*configuration, subject, recipients, replyTo)
}

// RenderEmail is render_to_string(name, context) in the worker (instance
// branding, LANGUAGE_CODE); inputs the renderer refuses are ErrEmailTaskUnsupported.
func (s *EmailTasks) RenderEmail(ctx context.Context, job domain.Job, name string, context map[string]any) (string, error) {
	reader, err := s.store.BeginEmailTask(ctx, &job)
	if err != nil {
		return "", err
	}
	defer reader.Rollback()
	return s.render(ctx, reader, name, context)
}

// --- smtpsend adapter -------------------------------------------------------------

// SMTPMailer sends through smtpsend with SMTP_TIMEOUT_SECONDS.
type SMTPMailer struct{ LocalHostname string }

func (m SMTPMailer) server(server domain.SMTPServer) smtpsend.Server {
	return smtpsend.Server{Host: server.Host, Port: server.Port, User: server.User, Password: server.Password,
		Encryption: server.Encryption, Timeout: 10 * time.Second, LocalHostname: m.LocalHostname}
}

func (m SMTPMailer) Verify(ctx context.Context, server domain.SMTPServer) error {
	return smtpsend.Verify(ctx, m.server(server))
}

func (m SMTPMailer) render(server domain.SMTPServer, message domain.SMTPMessage) (string, error) {
	rendered, err := smtpsend.Render(smtpsend.Message{SenderName: message.SenderName, SenderUser: message.SenderUser, To: message.To,
		Subject: message.Subject, ReplyTo: message.ReplyTo, HTML: message.HTML}, smtpsend.NewBoundary())
	if err == nil {
		err = smtpsend.CheckEnvelope(server.User, message.To)
	}
	if errors.Is(err, smtpsend.ErrUnsupported) {
		return "", errors.Join(domain.ErrEmailTaskUnsupported, err)
	}
	return rendered, err
}

func (m SMTPMailer) Check(server domain.SMTPServer, message domain.SMTPMessage) error {
	_, err := m.render(server, message)
	return err
}

func (m SMTPMailer) Send(ctx context.Context, server domain.SMTPServer, message domain.SMTPMessage) error {
	rendered, err := m.render(server, message)
	if err != nil {
		return err
	}
	return smtpsend.Send(ctx, m.server(server), server.User, message.To, rendered)
}
