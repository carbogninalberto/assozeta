package service

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"log/slog"
	"net"
	"os"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celeryworker"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/smtpsend"
	"github.com/google/uuid"
)

// SendMailAsyncTask is core.tasks.send_mail_async's Celery name.
const SendMailAsyncTask = "core.tasks.send_mail_async"

// SendMailAsyncStore is the database side of the task.
type SendMailAsyncStore interface {
	ActiveAssociation(ctx context.Context, id uuid.UUID) (bool, error)
	FirstCommunicationConfiguration(ctx context.Context, association uuid.UUID) (*domain.CommunicationConfiguration, error)
	AssociationDenomination(ctx context.Context, association uuid.UUID) (*string, bool, error)
	SaveCommunicationConfiguration(ctx context.Context, configuration domain.CommunicationConfiguration, at time.Time) error
	InstanceBrand(ctx context.Context) (domain.EmailRenderInstanceConfig, error)
	InstanceEmailSettings(ctx context.Context) (*domain.ConsumerEmailInstance, error)
	InsertEmailLog(ctx context.Context, recipient, subject, result string, association *uuid.UUID) error
}

// SystemEmail is the environment source of email_configuration.effective_email
// (EMAIL_HOST, EMAIL_PORT or 465, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD,
// EMAIL_USE_TLS / EMAIL_USE_SSL).
type SystemEmail struct {
	Host, Username, Password string
	Port                     int64
	PortInvalid              bool
	UseTLS, UseSSL           bool
	// LocalHostname is DNS_NAME (socket.getfqdn()), the EHLO name and the
	// Message-ID domain.
	LocalHostname string
	// Dial and TLSConfig replace the network defaults in tests.
	Dial      func(ctx context.Context, network, address string) (net.Conn, error)
	TLSConfig *tls.Config
}

// SendMailAsync runs core.tasks.send_mail_async: custom_send_mail through
// instance.email_configuration.EmailBackend (environment or instance
// source, fail_silently), or CommunicationConfiguration.send_email for an
// association with a configuration (WithEmailTasks). Arguments outside the
// rendered subset are handed off to the Django worker before any effect.
// It also implements the application.tasks e-mail tasks (consumer_email.go).
type SendMailAsync struct {
	store SendMailAsyncStore
	email SystemEmail
	now   func() time.Time

	publisher celery.Publisher
	settings  ConsumerEmailSettings
	logger    *slog.Logger
	delay     func() time.Duration
	timers    sync.WaitGroup
}

func NewSendMailAsync(store SendMailAsyncStore, email SystemEmail, now func() time.Time) *SendMailAsync {
	return &SendMailAsync{store: store, email: email, now: now, logger: slog.New(slog.DiscardHandler)}
}

// Task registers the implementation: max_retries=3, default_retry_delay 180.
func (s *SendMailAsync) Task() celeryworker.Task {
	return celeryworker.Task{MaxRetries: 3, Plan: s.plan, Run: s.run}
}

// sendMailAsyncCall is the bound call after the plan.
type sendMailAsyncCall struct {
	subject, message, from, html string
	recipients, replyTo          []string
	association                  *uuid.UUID // the looked-up association (None when missing)
	silent                       bool       // fail_silently
	// source is effective_email's value for custom_send_mail.
	source sendMailAsyncSource
	// config is the CommunicationConfiguration.send_email call, when taken.
	config *consumerEmailConfigSend
}

// sendMailAsyncSource is effective_email(): the environment or, for a
// self-hosted instance with email settings, the instance's own transport
// and sender (the message's From replaced by formataddr).
type sendMailAsyncSource struct {
	instance                 bool
	host, username, password string
	port                     int64
	security                 string // tls, ssl, none; anything else is ImproperlyConfigured
	from                     string // the instance From value
	credentialError          bool   // decrypt_password raised ImproperlyConfigured
}

var sendMailAsyncPositional = []string{"subject", "message", "from_email", "recipient_list", "sport_association_id"}

// pythonTruth is bool(value) for the JSON scalars; ok is false otherwise.
func pythonTruth(value any) (truth, ok bool) {
	switch v := value.(type) {
	case nil:
		return false, true
	case bool:
		return v, true
	case string:
		return v != "", true
	case celery.Number:
		if strings.ContainsAny(string(v), ".eE") {
			return false, false
		}
		return strings.TrimLeft(strings.TrimPrefix(string(v), "-"), "0") != "", true
	}
	return false, false
}

func stringList(value any) ([]string, bool) {
	items, ok := value.([]any)
	if !ok {
		return nil, false
	}
	out := make([]string, len(items))
	for i, item := range items {
		if out[i], ok = item.(string); !ok {
			return nil, false
		}
	}
	return out, true
}

// bind applies the signature send_mail_async(self, subject, message,
// from_email, recipient_list, sport_association_id=None, **kwargs) and
// custom_send_mail's keyword parameters; anything else is handed off.
func (s *SendMailAsync) bind(request *celeryworker.Request) (map[string]any, error) {
	if len(request.Args) > len(sendMailAsyncPositional) {
		return nil, celeryworker.ErrHandoff
	}
	values := map[string]any{}
	for i, value := range request.Args {
		values[sendMailAsyncPositional[i]] = value
	}
	allowed := map[string]bool{"fail_silently": true, "html_message": true, "reply_to": true, "skip_association_check": true}
	for _, item := range request.Kwargs {
		if _, duplicate := values[item.Key]; duplicate {
			return nil, celeryworker.ErrHandoff
		}
		if !allowed[item.Key] && !containsString(sendMailAsyncPositional, item.Key) {
			return nil, celeryworker.ErrHandoff
		}
		values[item.Key] = item.Value
	}
	for _, name := range sendMailAsyncPositional[:4] {
		if _, present := values[name]; !present {
			return nil, celeryworker.ErrHandoff
		}
	}
	return values, nil
}

func containsString(list []string, value string) bool {
	for _, item := range list {
		if item == value {
			return true
		}
	}
	return false
}

func (s *SendMailAsync) plan(ctx context.Context, request *celeryworker.Request) error {
	values, err := s.bind(request)
	if err != nil {
		return err
	}
	call := &sendMailAsyncCall{}
	var ok bool
	if call.subject, ok = values["subject"].(string); !ok || utf8.RuneCountInString(call.subject) > 255 {
		return celeryworker.ErrHandoff
	}
	switch v := values["message"].(type) {
	case nil:
	case string:
		call.message = v
	default:
		return celeryworker.ErrHandoff
	}
	if call.from, ok = values["from_email"].(string); !ok {
		return celeryworker.ErrHandoff
	}
	if call.recipients, ok = stringList(values["recipient_list"]); !ok || len(call.recipients) == 0 ||
		utf8.RuneCountInString(call.recipients[0]) > 254 {
		return celeryworker.ErrHandoff
	}
	switch v := values["html_message"].(type) {
	case nil:
	case string:
		call.html = v
	default:
		return celeryworker.ErrHandoff
	}
	if reply, present := values["reply_to"]; present && reply != nil {
		if call.replyTo, ok = stringList(reply); !ok {
			return celeryworker.ErrHandoff
		}
	}
	if call.silent, ok = pythonTruth(values["fail_silently"]); !ok {
		return celeryworker.ErrHandoff
	}
	skipValue, skipPresent := values["skip_association_check"]
	skip, ok := pythonTruth(skipValue)
	if !ok {
		return celeryworker.ErrHandoff
	}
	var association *uuid.UUID
	switch v := values["sport_association_id"].(type) {
	case nil:
	case uuid.UUID:
		association = &v
	case string:
		if v != "" {
			parsed, err := uuid.Parse(v)
			if err != nil || len(v) != 36 && len(v) != 32 {
				return celeryworker.ErrHandoff
			}
			association = &parsed
		}
	default:
		return celeryworker.ErrHandoff
	}
	// The reads Django performs before sending; a failure here is left to run().
	if association != nil {
		found, err := s.store.ActiveAssociation(ctx, *association)
		if err != nil {
			return err
		}
		if found {
			call.association = association
			if !skipPresent || !skip {
				configuration, err := s.store.FirstCommunicationConfiguration(ctx, *association)
				if err != nil {
					return err
				}
				if configuration != nil {
					if s.publisher == nil {
						return celeryworker.ErrHandoff
					}
					// config.send_email(subject, body=message, recipient_list,
					// html_body=kwargs.get('html_message'), reply_to=kwargs.get('reply_to')).
					var body, html *string
					if message, present := values["message"]; present && message != nil {
						body = &call.message
					}
					if value, present := values["html_message"]; present && value != nil {
						html = &call.html
					}
					if call.config, err = s.planConfigSend(ctx, *configuration, call.subject, body, html, values["recipient_list"],
						values["reply_to"]); err != nil {
						return err
					}
					request.State = call
					return nil
				}
			}
		}
	}
	if err = s.planSource(ctx, call); err != nil {
		return err
	}
	if _, err = smtpsend.DjangoEnvelope(call.envelopeFrom()); err != nil {
		return celeryworker.ErrHandoff
	}
	if err = smtpsend.CheckDjangoMessage(smtpsend.DjangoMessage{Subject: call.subject, Body: call.message, HTML: call.html,
		From: call.envelopeFrom(), To: call.recipients, ReplyTo: call.replyTo}); err != nil {
		return celeryworker.ErrHandoff
	}
	request.State = call
	return nil
}

// envelopeFrom is the From the backend sends: the instance's formataddr
// value, or the message's from_email.
func (c *sendMailAsyncCall) envelopeFrom() string {
	if c.source.instance {
		return c.source.from
	}
	return c.from
}

var sendMailAsyncInstanceKeys = []string{"host", "port", "username", "security", "from_email", "sender_name"}

// planSource reads effective_email(): the instance settings when the
// configuration is self-hosted with truthy email settings, else the
// environment. Instance settings outside the modelled shape are handed off.
func (s *SendMailAsync) planSource(ctx context.Context, call *sendMailAsyncCall) error {
	row, err := s.store.InstanceEmailSettings(ctx)
	if err != nil {
		return err
	}
	var settings any
	if row != nil {
		decoder := json.NewDecoder(strings.NewReader(row.EmailSettings))
		decoder.UseNumber()
		if decoder.Decode(&settings) != nil {
			return celeryworker.ErrHandoff
		}
	}
	if row == nil || !row.SelfHosted || !instanceTruthy(settings) {
		if s.email.PortInvalid || s.email.Host == "" {
			return celeryworker.ErrHandoff
		}
		security := "none"
		switch {
		case s.email.UseTLS && s.email.UseSSL:
			security = "invalid"
		case s.email.UseTLS:
			security = "tls"
		case s.email.UseSSL:
			security = "ssl"
		}
		call.source = sendMailAsyncSource{host: s.email.Host, username: s.email.Username, password: s.email.Password,
			port: s.email.Port, security: security}
		return nil
	}
	object, ok := settings.(map[string]any)
	if !ok {
		return celeryworker.ErrHandoff
	}
	text := map[string]string{}
	for _, key := range sendMailAsyncInstanceKeys {
		if key == "port" {
			continue
		}
		if text[key], ok = object[key].(string); !ok {
			return celeryworker.ErrHandoff
		}
	}
	number, ok := object["port"].(json.Number)
	port, err := strconv.ParseInt(string(number), 10, 64)
	if !ok || err != nil || port < 1 || port > 65535 || strconv.FormatInt(port, 10) != string(number) || text["host"] == "" {
		return celeryworker.ErrHandoff
	}
	source := sendMailAsyncSource{instance: true, host: text["host"], username: text["username"], port: port, security: text["security"]}
	if row.PasswordEncrypted != "" {
		source.credentialError = true
		for _, secret := range s.settings.SecretKeys {
			if plain, opened := fernetOpen(row.PasswordEncrypted, instanceAdminFernetKey(instanceEmailCipher, secret)); opened {
				if !utf8.Valid(plain) {
					return celeryworker.ErrHandoff
				}
				source.password, source.credentialError = string(plain), false
				break
			}
		}
	}
	// formataddr((sender_name, from_email)); quoted or encoded names are
	// outside the subset of the rendered From.
	if strings.ContainsAny(text["sender_name"], `"\`) || !isASCIIText(text["sender_name"]) {
		return celeryworker.ErrHandoff
	}
	if source.from, err = smtpsend.FormatAddr(text["sender_name"], text["from_email"]); err != nil || strings.Contains(source.from, `"`) {
		return celeryworker.ErrHandoff
	}
	call.source = source
	return nil
}

func isASCIIText(text string) bool {
	for i := 0; i < len(text); i++ {
		if text[i] >= 0x80 {
			return false
		}
	}
	return true
}

// retry is the task's except branch: self.retry(exc=exc,
// countdown=int(default_retry_delay * 2 ** request.retries)).
func sendMailAsyncRetry(request *celeryworker.Request, exc *celeryworker.Exception) error {
	return &celeryworker.Retry{Exc: exc, Countdown: 180 << min(request.Retries, 40)}
}

var errSendMailAsyncDatabase = &celeryworker.Exception{Type: "OperationalError", Module: "django.db.utils", Args: []any{"database error"}}

func (s *SendMailAsync) run(ctx context.Context, request *celeryworker.Request) (any, error) {
	call, ok := request.State.(*sendMailAsyncCall)
	if !ok {
		// The plan's reads failed: the task body raises on the same read.
		return nil, sendMailAsyncRetry(request, errSendMailAsyncDatabase)
	}
	if call.config != nil {
		s.configSend(ctx, request, call.config)
	} else if exc := s.send(ctx, call); exc != nil {
		return nil, sendMailAsyncRetry(request, exc)
	}
	if err := s.store.InsertEmailLog(ctx, call.recipients[0], call.subject, "Sent", call.association); err != nil {
		return nil, sendMailAsyncRetry(request, errSendMailAsyncDatabase)
	}
	return nil, nil
}

// send is custom_send_mail through instance.email_configuration.EmailBackend
// with the environment source: smtp_connection, open(), _send, close().
func (s *SendMailAsync) send(ctx context.Context, call *sendMailAsyncCall) *celeryworker.Exception {
	source := call.source
	if source.credentialError {
		return &celeryworker.Exception{Type: "ImproperlyConfigured", Module: "django.core.exceptions",
			Args: []any{"La credenziale email salvata deve essere reinserita."}}
	}
	encryption := ""
	switch source.security {
	case "tls":
		encryption = "TLS"
	case "ssl":
		encryption = "SSL"
	case "none":
	default:
		return &celeryworker.Exception{Type: "ImproperlyConfigured", Module: "django.core.exceptions",
			Args: []any{"Scegli una sola modalità di sicurezza SMTP: TLS o SSL."}}
	}
	local := s.localHostname()
	config := s.email.TLSConfig
	if config == nil {
		// ssl.create_default_context(): certificate and host name verification.
		config = &tls.Config{ServerName: source.host, MinVersion: tls.VersionTLS12}
	}
	server := smtpsend.Server{Host: source.host, Port: int(source.port), User: source.username, Password: source.password,
		Encryption: encryption, Timeout: 5 * time.Second, LocalHostname: local, Dial: s.email.Dial, TLSConfig: config}
	now := s.now()
	message, err := smtpsend.RenderDjangoMessage(smtpsend.DjangoMessage{Subject: call.subject, Body: call.message, HTML: call.html,
		From: call.envelopeFrom(), To: call.recipients, ReplyTo: call.replyTo, Date: now, MessageID: smtpsend.MessageID(now, local),
		Boundary: smtpsend.NewBoundary()})
	if err != nil {
		return &celeryworker.Exception{Type: "ValueError", Module: "builtins", Args: []any{"message outside the rendered subset"}}
	}
	from, _ := smtpsend.DjangoEnvelope(call.envelopeFrom())
	backend, err := smtpsend.OpenDjangoBackend(ctx, server)
	if err != nil {
		// open(): fail_silently swallows OSError (smtplib's errors included).
		if call.silent && sendMailAsyncOSError(err) {
			return nil
		}
		return pythonSMTPException(err, from)
	}
	err = backend.SendMail(from, call.recipients, message)
	backend.Close()
	if err != nil && !(call.silent && sendMailAsyncSMTPException(err)) {
		return pythonSMTPException(err, from)
	}
	return nil
}

// dnsHostname is django.core.mail.utils.DNS_NAME (socket.getfqdn()).
func dnsHostname() string {
	host, _ := os.Hostname()
	return smtpsend.FQDN(host)
}

// sendMailAsyncOSError reports an error Python raises as an OSError subclass.
func sendMailAsyncOSError(err error) bool {
	switch pythonSMTPException(err, "").Type {
	case "UnicodeEncodeError", "ValueError", "binascii.Error":
		return false
	}
	return true
}

// sendMailAsyncSMTPException reports an smtplib.SMTPException (what _send
// swallows with fail_silently); socket-level errors are not.
func sendMailAsyncSMTPException(err error) bool {
	exc := pythonSMTPException(err, "")
	return exc.Module == "smtplib"
}

// pythonBytes is kombu's JSON form of a bytes exception argument; text that
// is not UTF-8 falls back to ensure_serializable's repr.
func pythonBytes(text string) any {
	if utf8.ValidString(text) {
		return celery.Dict{{Key: "__type__", Value: "bytes"}, {Key: "__value__", Value: text}}
	}
	var out strings.Builder
	out.WriteString("b'")
	for i := 0; i < len(text); i++ {
		c := text[i]
		switch {
		case c == '\\' || c == '\'':
			out.WriteString("\\" + string(c))
		case c == '\t':
			out.WriteString(`\t`)
		case c == '\n':
			out.WriteString(`\n`)
		case c == '\r':
			out.WriteString(`\r`)
		case c < 0x20 || c >= 0x7f:
			out.WriteString(`\x` + strconv.FormatUint(uint64(c)|0x100, 16)[1:])
		default:
			out.WriteByte(c)
		}
	}
	out.WriteByte('\'')
	return out.String()
}

// errnoText is the C library's strerror for the errors a connection gives.
func errnoText(errno syscall.Errno) string {
	switch errno {
	case syscall.ECONNREFUSED:
		return "Connection refused"
	case syscall.ECONNRESET:
		return "Connection reset by peer"
	case syscall.EHOSTUNREACH:
		return "No route to host"
	case syscall.ENETUNREACH:
		return "Network is unreachable"
	}
	text := errno.Error()
	if text != "" {
		text = strings.ToUpper(text[:1]) + text[1:]
	}
	return text
}

// pythonSMTPException is the exception smtplib (or the socket layer) raises
// for err, as the result backend stores it. Reply errors carry their code
// and reply bytes; connection errors their errno and strerror.
func pythonSMTPException(err error, from string) *celeryworker.Exception {
	var smtpErr *smtpsend.Error
	if errors.As(err, &smtpErr) {
		exc := &celeryworker.Exception{Type: smtpErr.Kind, Module: "smtplib"}
		switch smtpErr.Kind {
		case "SMTPAuthenticationError", "SMTPDataError", "SMTPConnectError", "SMTPHeloError", "SMTPResponseException":
			exc.Args = []any{int64(smtpErr.Code), pythonBytes(smtpErr.Msg)}
		case "SMTPSenderRefused":
			exc.Args = []any{int64(smtpErr.Code), pythonBytes(smtpErr.Msg), from}
		case "SMTPServerDisconnected":
			exc.Args = []any{"Connection unexpectedly closed"}
		case "UnicodeEncodeError", "ValueError", "binascii.Error":
			exc.Module = "builtins"
		}
		return exc
	}
	var netErr net.Error
	if errors.As(err, &netErr) && netErr.Timeout() {
		return &celeryworker.Exception{Type: "TimeoutError", Module: "builtins", Args: []any{"timed out"}}
	}
	var errno syscall.Errno
	if errors.As(err, &errno) {
		kind := "OSError"
		switch errno {
		case syscall.ECONNREFUSED:
			kind = "ConnectionRefusedError"
		case syscall.ECONNRESET:
			kind = "ConnectionResetError"
		case syscall.ETIMEDOUT:
			kind = "TimeoutError"
		}
		return &celeryworker.Exception{Type: kind, Module: "builtins", Args: []any{int64(errno), errnoText(errno)}}
	}
	var certErr *tls.CertificateVerificationError
	if errors.As(err, &certErr) {
		return &celeryworker.Exception{Type: "SSLCertVerificationError", Module: "ssl"}
	}
	return &celeryworker.Exception{Type: "OSError", Module: "builtins"}
}
