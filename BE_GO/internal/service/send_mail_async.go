package service

import (
	"context"
	"crypto/tls"
	"errors"
	"net"
	"os"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celeryworker"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/smtpsend"
	"github.com/google/uuid"
)

// SendMailAsyncTask is core.tasks.send_mail_async's Celery name.
const SendMailAsyncTask = "core.tasks.send_mail_async"

// SendMailAsyncStore is the database side of the task.
type SendMailAsyncStore interface {
	ActiveAssociation(ctx context.Context, id uuid.UUID) (bool, error)
	HasCommunicationConfiguration(ctx context.Context, association uuid.UUID) (bool, error)
	InstanceEmail(ctx context.Context) (bool, error)
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

// SendMailAsync runs core.tasks.send_mail_async for the inputs that take
// custom_send_mail with the system backend (no association, a deleted or
// missing one, an association without CommunicationConfiguration, or
// skip_association_check). The CommunicationConfiguration.send_email path,
// the instance email source and arguments outside the rendered subset are
// handed off to the Django worker before any effect.
type SendMailAsync struct {
	store SendMailAsyncStore
	email SystemEmail
	now   func() time.Time
}

func NewSendMailAsync(store SendMailAsyncStore, email SystemEmail, now func() time.Time) *SendMailAsync {
	return &SendMailAsync{store: store, email: email, now: now}
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
	if silent, ok := pythonTruth(values["fail_silently"]); !ok || silent {
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
	if s.email.PortInvalid || s.email.Host == "" {
		return celeryworker.ErrHandoff
	}
	if _, err = smtpsend.DjangoEnvelope(call.from); err != nil {
		return celeryworker.ErrHandoff
	}
	if err = smtpsend.CheckDjangoMessage(smtpsend.DjangoMessage{Subject: call.subject, Body: call.message, HTML: call.html,
		From: call.from, To: call.recipients, ReplyTo: call.replyTo}); err != nil {
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
				configured, err := s.store.HasCommunicationConfiguration(ctx, *association)
				if err != nil {
					return err
				}
				if configured {
					return celeryworker.ErrHandoff // CommunicationConfiguration.send_email
				}
			}
		}
	}
	instance, err := s.store.InstanceEmail(ctx)
	if err != nil {
		return err
	}
	if instance {
		return celeryworker.ErrHandoff
	}
	request.State = call
	return nil
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
	if exc := s.send(ctx, call); exc != nil {
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
	encryption := ""
	switch {
	case s.email.UseTLS && s.email.UseSSL:
		return &celeryworker.Exception{Type: "ImproperlyConfigured", Module: "django.core.exceptions",
			Args: []any{"Scegli una sola modalità di sicurezza SMTP: TLS o SSL."}}
	case s.email.UseTLS:
		encryption = "TLS"
	case s.email.UseSSL:
		encryption = "SSL"
	}
	local := s.email.LocalHostname
	if local == "" {
		host, _ := os.Hostname()
		local = smtpsend.FQDN(host)
	}
	config := s.email.TLSConfig
	if config == nil {
		// ssl.create_default_context(): certificate and host name verification.
		config = &tls.Config{ServerName: s.email.Host, MinVersion: tls.VersionTLS12}
	}
	server := smtpsend.Server{Host: s.email.Host, Port: int(s.email.Port), User: s.email.Username, Password: s.email.Password,
		Encryption: encryption, Timeout: 5 * time.Second, LocalHostname: local, Dial: s.email.Dial, TLSConfig: config}
	now := s.now()
	message, err := smtpsend.RenderDjangoMessage(smtpsend.DjangoMessage{Subject: call.subject, Body: call.message, HTML: call.html,
		From: call.from, To: call.recipients, ReplyTo: call.replyTo, Date: now, MessageID: smtpsend.MessageID(now, local),
		Boundary: smtpsend.NewBoundary()})
	if err != nil {
		return &celeryworker.Exception{Type: "ValueError", Module: "builtins", Args: []any{"message outside the rendered subset"}}
	}
	from, _ := smtpsend.DjangoEnvelope(call.from)
	backend, err := smtpsend.OpenDjangoBackend(ctx, server)
	if err != nil {
		return pythonSMTPException(err, from)
	}
	err = backend.SendMail(from, call.recipients, message)
	backend.Close()
	if err != nil {
		return pythonSMTPException(err, from)
	}
	return nil
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
