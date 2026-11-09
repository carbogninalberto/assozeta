package service

import (
	"context"
	"encoding/json"
	"log/slog"
	"math/rand/v2"
	"regexp"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celeryworker"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/emailrender"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/smtpsend"
	"github.com/google/uuid"
)

// Celery names of the application.tasks e-mail tasks (@shared_task(name=...)).
const (
	SendEmailTextTask     = "send_email_text"
	SendEmailTemplateTask = "send_email_template"
	SendEmailHTMLTask     = "send_email_html"
)

// ConsumerEmailSettings are the core.settings values the e-mail tasks read in
// the worker.
type ConsumerEmailSettings struct {
	AppURL, WhitelabelName, DefaultFromEmail, DefaultTeamEmail string
	IsWhitelabel                                               bool
	// SecretKeys are SECRET_KEY then SECRET_KEY_FALLBACKS (decrypt_password).
	SecretKeys []string
}

// WithEmailTasks enables the CommunicationConfiguration.send_email path, the
// instance email source and the application.tasks e-mail tasks: publisher
// sends the send_mail_async messages a task publishes (routed like
// Django's producer).
func (s *SendMailAsync) WithEmailTasks(publisher celery.Publisher, settings ConsumerEmailSettings, logger *slog.Logger) *SendMailAsync {
	s.publisher, s.settings = publisher, settings
	if logger != nil {
		s.logger = logger
	}
	if s.delay == nil {
		// send_email_html: random.randint(1, 30) seconds.
		s.delay = func() time.Duration { return time.Duration(1+rand.IntN(30)) * time.Second }
	}
	return s
}

// Wait blocks until the delayed sends send_email_html started have ended, as
// the worker process waits for the task's non-daemon Timer threads.
func (s *SendMailAsync) Wait() { s.timers.Wait() }

// --- values ---------------------------------------------------------------------

var consumerEmailCanonicalInt = regexp.MustCompile(`^-?(0|[1-9][0-9]{0,17})$`)

// consumerEmailPassThrough reports a decoded argument the task forwards to a
// message it publishes and that the producer re-encodes exactly as kombu
// would after json.loads: None, bools, strings, UUIDs, canonical integers and
// lists or dicts (without repeated keys) of these.
func consumerEmailPassThrough(value any) bool {
	switch v := value.(type) {
	case nil, bool, string, uuid.UUID:
		return true
	case celery.Number:
		return consumerEmailCanonicalInt.MatchString(string(v)) && string(v) != "-0"
	case []any:
		for _, item := range v {
			if !consumerEmailPassThrough(item) {
				return false
			}
		}
		return true
	case celery.Dict:
		seen := map[string]bool{}
		for _, item := range v {
			if seen[item.Key] || !consumerEmailPassThrough(item.Value) {
				return false
			}
			seen[item.Key] = true
		}
		return true
	}
	return false
}

// consumerEmailAssociation reads a sport_association_id argument used in a
// queryset filter: a UUID or its string forms; ok is false for values the
// UUID field would reject (ValidationError) or other types.
func consumerEmailAssociation(value any) (id uuid.UUID, ok bool) {
	switch v := value.(type) {
	case uuid.UUID:
		return v, true
	case string:
		parsed, err := uuid.Parse(v)
		if err != nil || len(v) != 36 && len(v) != 32 {
			return uuid.Nil, false
		}
		return parsed, true
	}
	return uuid.Nil, false
}

// consumerEmailBind applies a Python signature with defaults to the
// message's args and kwargs; a TypeError the call would raise is a handoff.
func consumerEmailBind(request *celeryworker.Request, names []string, required int) (map[string]any, error) {
	if len(request.Args) > len(names) {
		return nil, celeryworker.ErrHandoff
	}
	values := map[string]any{}
	for i, value := range request.Args {
		values[names[i]] = value
	}
	for _, item := range request.Kwargs {
		if _, duplicate := values[item.Key]; duplicate || !containsString(names, item.Key) {
			return nil, celeryworker.ErrHandoff
		}
		values[item.Key] = item.Value
	}
	for _, name := range names[:required] {
		if _, present := values[name]; !present {
			return nil, celeryworker.ErrHandoff
		}
	}
	return values, nil
}

// consumerEmailReplyTo is the Reply-To send_email writes: a truthy list is
// joined, a truthy string kept; ok is false for values the join rejects.
func consumerEmailReplyTo(value any) (list []string, ok bool) {
	switch v := value.(type) {
	case nil:
		return nil, true
	case string:
		if v == "" {
			return nil, true
		}
		return []string{v}, true
	case []any:
		return stringList(v)
	}
	return nil, false
}

// --- child messages ---------------------------------------------------------------

// consumerEmailPublish is send_mail_async.apply_async(kwargs=...): from a
// running task (request non-nil) the message carries its root and parent
// ids; from a Timer thread it does not. add_to_parent appends the result to
// task.request, a copy of the request the tracer stores a SUCCESS with, so
// the stored meta keeps no children (Celery 5.6 trace.build_tracer).
func (s *SendMailAsync) consumerEmailPublish(ctx context.Context, request *celeryworker.Request, kwargs celery.Dict) error {
	task := celery.Task{Name: SendMailAsyncTask, Kwargs: kwargs}
	if request != nil {
		task.RootID, task.ParentID = request.RootID, request.ID
		if task.RootID == "" {
			task.RootID = request.ID
		}
	}
	_, err := s.publisher.Publish(ctx, task)
	return err
}

// --- CommunicationConfiguration.send_email ---------------------------------------

// consumerEmailConfigSend is one send_email(subject, body, recipient_list,
// html_body, reply_to) call on a loaded configuration.
type consumerEmailConfigSend struct {
	configuration domain.CommunicationConfiguration
	subject       string
	body          *string // None or str
	html          *string // None or str
	recipients    any     // the argument as given (forwarded on fallback)
	replyTo       any
	to            []string
	replyHeader   []string
	// denomination is configuration.sport_association.denomination; nil
	// when the relation or the value is None (the fallback then raises).
	denomination *string
}

func (c *consumerEmailConfigSend) htmlBody() string {
	if c.html == nil {
		return ""
	}
	return *c.html
}

// smtpMessage is the MIMEMultipart send_email builds; ok is false when
// MIMEText(body) raises (no HTML and a None body).
func (c *consumerEmailConfigSend) smtpMessage() (smtpsend.Message, bool) {
	sender := ""
	if c.configuration.SenderName != nil {
		sender = *c.configuration.SenderName
	}
	message := smtpsend.Message{SenderName: sender, SenderUser: *c.configuration.User, To: c.to, Subject: c.subject,
		ReplyTo: c.replyHeader}
	switch {
	case c.htmlBody() != "":
		message.HTML = c.htmlBody()
	case c.body == nil:
		return message, false
	default:
		message.Text = *c.body
	}
	return message, true
}

// planConfigSend loads what send_email reads and refuses, before any effect,
// a call whose SMTP message or envelope is outside the smtpsend subset.
func (s *SendMailAsync) planConfigSend(ctx context.Context, configuration domain.CommunicationConfiguration, subject string,
	body, html *string, recipients, replyTo any) (*consumerEmailConfigSend, error) {
	call := &consumerEmailConfigSend{configuration: configuration, subject: subject, body: body, html: html,
		recipients: recipients, replyTo: replyTo}
	var ok bool
	if call.to, ok = stringList(recipients); !ok {
		return nil, celeryworker.ErrHandoff
	}
	if call.replyHeader, ok = consumerEmailReplyTo(replyTo); !ok {
		return nil, celeryworker.ErrHandoff
	}
	if !consumerEmailPassThrough(recipients) || !consumerEmailPassThrough(replyTo) {
		return nil, celeryworker.ErrHandoff
	}
	if configuration.DailyBalance >= configuration.DailyLimit {
		return call, nil
	}
	if smtpConfigured(configuration) {
		if message, ok := call.smtpMessage(); ok {
			if _, err := smtpsend.Render(message, smtpsend.NewBoundary()); err != nil {
				return nil, celeryworker.ErrHandoff
			}
			if err := smtpsend.CheckEnvelope(*configuration.User, call.to); err != nil {
				return nil, celeryworker.ErrHandoff
			}
		}
	}
	if configuration.AssociationID != nil {
		denomination, _, err := s.store.AssociationDenomination(ctx, *configuration.AssociationID)
		if err != nil {
			return nil, err
		}
		call.denomination = denomination
	}
	return call, nil
}

// configSend is send_email: the daily limit, then verify_smtp(send_email=False)
// and the delivery with the balance save, or send_mail_async under the
// association's name. Its own try/except returns False on any failure, so
// nothing here is raised to the task.
func (s *SendMailAsync) configSend(ctx context.Context, request *celeryworker.Request, call *consumerEmailConfigSend) {
	configuration := call.configuration
	if configuration.DailyBalance >= configuration.DailyLimit {
		return
	}
	mailer := SMTPMailer{LocalHostname: s.localHostname()}
	if smtpConfigured(configuration) {
		server := smtpServer(configuration)
		if err := mailer.Verify(ctx, server); err == nil {
			message, ok := call.smtpMessage()
			if !ok {
				return
			}
			rendered, err := smtpsend.Render(message, smtpsend.NewBoundary())
			if err == nil {
				err = smtpsend.Send(ctx, mailer.server(server), server.User, call.to, rendered)
			}
			if err != nil {
				s.logger.Warn("communication smtp send failed", "code", "communication_smtp_failed")
				return
			}
			configuration.DailyBalance++
			if err = s.store.SaveCommunicationConfiguration(ctx, configuration, s.now().UTC().Truncate(time.Microsecond)); err != nil {
				s.logger.Warn("communication configuration save failed", "code", "communication_balance_failed")
			}
			return
		}
	}
	if configuration.AssociationID == nil || call.denomination == nil {
		return // self.sport_association is None, or str.replace(..., None)
	}
	kwargs := celery.Dict{{Key: "subject", Value: call.subject}, {Key: "message", Value: optionalValue(call.body)},
		{Key: "from_email", Value: strings.ReplaceAll(s.settings.DefaultFromEmail, s.settings.WhitelabelName, *call.denomination)},
		{Key: "recipient_list", Value: call.recipients}, {Key: "fail_silently", Value: false}}
	if call.htmlBody() != "" {
		kwargs = append(kwargs, celery.Item{Key: "html_message", Value: call.htmlBody()})
	}
	kwargs = append(kwargs, celery.Item{Key: "sport_association_id", Value: *configuration.AssociationID},
		celery.Item{Key: "reply_to", Value: call.replyTo}, celery.Item{Key: "skip_association_check", Value: true})
	if err := s.consumerEmailPublish(ctx, request, kwargs); err != nil {
		s.logger.Warn("send_mail_async publish failed", "code", "send_mail_async_publish_failed")
	}
}

func (s *SendMailAsync) localHostname() string {
	if s.email.LocalHostname != "" {
		return s.email.LocalHostname
	}
	return dnsHostname()
}

// --- send_email_text ----------------------------------------------------------------

type consumerEmailForward struct {
	message, subject, recipients, association, replyTo any
}

// SendEmailTextTask is send_email_text(message, subject, recipient_list,
// sport_association_id=None, reply_to=None): it publishes send_mail_async
// with DEFAULT_TEAM_EMAIL; every exception is logged and swallowed.
func (s *SendMailAsync) SendEmailTextTask() celeryworker.Task {
	return celeryworker.Task{MaxRetries: 3, Plan: s.planEmailText, Run: s.runEmailText}
}

func (s *SendMailAsync) planEmailText(_ context.Context, request *celeryworker.Request) error {
	call, err := s.planForward(consumerEmailForwardNames, request)
	if err != nil {
		return err
	}
	request.State = call
	return nil
}

var consumerEmailForwardNames = []string{"message", "subject", "recipient_list", "sport_association_id", "reply_to"}

// planForward binds the (message|html_message, subject, recipient_list,
// sport_association_id, reply_to) signature of send_email_text/html.
func (s *SendMailAsync) planForward(names []string, request *celeryworker.Request) (*consumerEmailForward, error) {
	values, err := consumerEmailBind(request, names, 3)
	if err != nil {
		return nil, err
	}
	call := &consumerEmailForward{message: values[names[0]], subject: values["subject"], recipients: values["recipient_list"],
		association: values["sport_association_id"], replyTo: values["reply_to"]}
	for _, value := range []any{call.message, call.subject, call.recipients, call.association, call.replyTo} {
		if !consumerEmailPassThrough(value) {
			return nil, celeryworker.ErrHandoff
		}
	}
	return call, nil
}

func (s *SendMailAsync) runEmailText(ctx context.Context, request *celeryworker.Request) (any, error) {
	call, ok := request.State.(*consumerEmailForward)
	if !ok {
		return nil, nil
	}
	if err := s.consumerEmailPublish(ctx, request, celery.Dict{{Key: "subject", Value: call.subject},
		{Key: "message", Value: call.message}, {Key: "from_email", Value: s.settings.DefaultTeamEmail},
		{Key: "recipient_list", Value: call.recipients}, {Key: "fail_silently", Value: false},
		{Key: "sport_association_id", Value: call.association}, {Key: "reply_to", Value: call.replyTo}}); err != nil {
		s.logger.Warn("send_email_text publish failed", "code", "send_email_text_failed")
	}
	return nil, nil
}

// --- send_email_html ----------------------------------------------------------------

var consumerEmailHTMLNames = []string{"html_message", "subject", "recipient_list", "sport_association_id", "reply_to"}

type consumerEmailHTMLPlan struct {
	forward *consumerEmailForward
	config  *consumerEmailConfigSend // with a configuration: the delayed send_email
}

// SendEmailHTMLTask is send_email_html(html_message, subject, recipient_list,
// sport_association_id=None, reply_to=None): with a CommunicationConfiguration
// of the association, send_email runs on the loaded configuration from a
// Timer 1-30 s after the task returned; otherwise send_mail_async is
// published with DEFAULT_FROM_EMAIL.
func (s *SendMailAsync) SendEmailHTMLTask() celeryworker.Task {
	return celeryworker.Task{MaxRetries: 3, Plan: s.planEmailHTML, Run: s.runEmailHTML}
}

func (s *SendMailAsync) planEmailHTML(ctx context.Context, request *celeryworker.Request) error {
	forward, err := s.planForward(consumerEmailHTMLNames, request)
	if err != nil {
		return err
	}
	plan := &consumerEmailHTMLPlan{forward: forward}
	if forward.association != nil {
		association, ok := consumerEmailAssociation(forward.association)
		if !ok {
			return celeryworker.ErrHandoff
		}
		configuration, err := s.store.FirstCommunicationConfiguration(ctx, association)
		if err != nil {
			return err
		}
		if configuration != nil {
			html, ok := forward.message.(string)
			if forward.message != nil && !ok {
				return celeryworker.ErrHandoff
			}
			subject, ok := forward.subject.(string)
			if !ok {
				return celeryworker.ErrHandoff
			}
			var htmlBody *string
			if forward.message != nil {
				htmlBody = &html
			}
			if plan.config, err = s.planConfigSend(ctx, *configuration, subject, nil, htmlBody, forward.recipients, forward.replyTo); err != nil {
				return err
			}
		}
	}
	request.State = plan
	return nil
}

func (s *SendMailAsync) runEmailHTML(ctx context.Context, request *celeryworker.Request) (any, error) {
	plan, ok := request.State.(*consumerEmailHTMLPlan)
	if !ok {
		return nil, nil // a read failed: the task's except branch
	}
	if plan.config != nil {
		call := plan.config
		s.timers.Add(1)
		time.AfterFunc(s.delay(), func() {
			defer s.timers.Done()
			timerCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 2*time.Minute)
			defer cancel()
			s.configSend(timerCtx, nil, call)
		})
		return nil, nil
	}
	call := plan.forward
	if err := s.consumerEmailPublish(ctx, request, celery.Dict{{Key: "subject", Value: call.subject},
		{Key: "message", Value: call.message}, {Key: "from_email", Value: s.settings.DefaultFromEmail},
		{Key: "recipient_list", Value: call.recipients}, {Key: "html_message", Value: call.message},
		{Key: "fail_silently", Value: false}, {Key: "sport_association_id", Value: call.association},
		{Key: "reply_to", Value: call.replyTo}}); err != nil {
		s.logger.Warn("send_email_html publish failed", "code", "send_email_html_failed")
	}
	return nil, nil
}

// --- send_email_template -------------------------------------------------------------

var consumerEmailTemplateNames = []string{"template", "subject", "recipient_list", "data", "sport_association_id", "reply_to"}

type consumerEmailTemplatePlan struct {
	message                          string
	subject, recipients, association any
	replyTo                          any
	config                           *consumerEmailConfigSend
}

// SendEmailTemplateTask is send_email_template(template, subject,
// recipient_list, data, sport_association_id=None, reply_to=None):
// render_to_string, then send_email on the association's configuration in
// the task, or send_mail_async with DEFAULT_TEAM_EMAIL.
func (s *SendMailAsync) SendEmailTemplateTask() celeryworker.Task {
	return celeryworker.Task{MaxRetries: 3, Plan: s.planEmailTemplate, Run: s.runEmailTemplate}
}

// consumerEmailContext converts the decoded data dict into the renderer's
// context values; ok is false for values the renderer does not model.
func consumerEmailContext(value any) (any, bool) {
	switch v := value.(type) {
	case nil, bool, string:
		return v, true
	case celery.Number:
		if !consumerEmailCanonicalInt.MatchString(string(v)) || string(v) == "-0" {
			return nil, false
		}
		var n int64
		if err := json.Unmarshal([]byte(v), &n); err != nil {
			return nil, false
		}
		return n, true
	case celery.Dict:
		out := map[string]any{}
		for _, item := range v {
			if _, repeated := out[item.Key]; repeated {
				return nil, false
			}
			converted, ok := consumerEmailContext(item.Value)
			if !ok {
				return nil, false
			}
			out[item.Key] = converted
		}
		return out, true
	}
	return nil, false
}

func (s *SendMailAsync) planEmailTemplate(ctx context.Context, request *celeryworker.Request) error {
	values, err := consumerEmailBind(request, consumerEmailTemplateNames, 4)
	if err != nil {
		return err
	}
	name, ok := values["template"].(string)
	if !ok {
		return celeryworker.ErrHandoff
	}
	context := map[string]any{}
	if data := values["data"]; data != nil {
		converted, ok := consumerEmailContext(data)
		if !ok {
			return celeryworker.ErrHandoff
		}
		if context, ok = converted.(map[string]any); !ok {
			return celeryworker.ErrHandoff
		}
	}
	plan := &consumerEmailTemplatePlan{subject: values["subject"], recipients: values["recipient_list"],
		association: values["sport_association_id"], replyTo: values["reply_to"]}
	for _, value := range []any{plan.subject, plan.recipients, plan.association, plan.replyTo} {
		if !consumerEmailPassThrough(value) {
			return celeryworker.ErrHandoff
		}
	}
	stored, err := s.store.InstanceBrand(ctx)
	if err != nil {
		return err
	}
	var config *emailrender.InstanceConfig
	if stored.Found {
		config = &emailrender.InstanceConfig{SelfHosted: stored.SelfHosted, PrimaryColor: stored.PrimaryColor, Name: stored.Name,
			LogoPath: stored.LogoPath, UpdatedAt: stored.UpdatedAt}
	}
	brand, err := emailrender.Brand(config, emailrender.Settings{AppURL: s.settings.AppURL, WhitelabelName: s.settings.WhitelabelName,
		IsWhitelabel: s.settings.IsWhitelabel})
	if err != nil {
		return celeryworker.ErrHandoff
	}
	// No language is activated in the worker: LANGUAGE_CODE.
	if plan.message, err = emailrender.Render(name, context, emailrender.Options{Language: "it", Brand: brand}); err != nil {
		return celeryworker.ErrHandoff
	}
	if plan.association != nil {
		association, ok := consumerEmailAssociation(plan.association)
		if !ok {
			return celeryworker.ErrHandoff
		}
		configuration, err := s.store.FirstCommunicationConfiguration(ctx, association)
		if err != nil {
			return err
		}
		if configuration != nil {
			subject, ok := plan.subject.(string)
			if !ok {
				return celeryworker.ErrHandoff
			}
			if plan.config, err = s.planConfigSend(ctx, *configuration, subject, nil, &plan.message, plan.recipients, plan.replyTo); err != nil {
				return err
			}
		}
	}
	request.State = plan
	return nil
}

func (s *SendMailAsync) runEmailTemplate(ctx context.Context, request *celeryworker.Request) (any, error) {
	plan, ok := request.State.(*consumerEmailTemplatePlan)
	if !ok {
		return nil, nil
	}
	if plan.config != nil {
		s.configSend(ctx, request, plan.config)
		return nil, nil
	}
	if err := s.consumerEmailPublish(ctx, request, celery.Dict{{Key: "subject", Value: plan.subject},
		{Key: "message", Value: plan.message}, {Key: "from_email", Value: s.settings.DefaultTeamEmail},
		{Key: "recipient_list", Value: plan.recipients}, {Key: "html_message", Value: plan.message},
		{Key: "fail_silently", Value: false}, {Key: "sport_association_id", Value: plan.association},
		{Key: "reply_to", Value: plan.replyTo}}); err != nil {
		s.logger.Warn("send_email_template publish failed", "code", "send_email_template_failed")
	}
	return nil, nil
}
