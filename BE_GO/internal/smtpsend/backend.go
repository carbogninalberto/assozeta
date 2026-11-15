package smtpsend

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"math/rand/v2"
	"os"
	"regexp"
	"strings"
	"time"
)

// Backend is django.core.mail.backends.smtp.EmailBackend(host, port,
// username, password, use_tls, use_ssl, timeout, fail_silently=False) used
// as a context manager: OpenBackend is __enter__ and Close is __exit__.
type Backend struct{ c *client }

// OpenBackend is EmailBackend.open() inside __enter__: smtplib.SMTP (or
// SMTP_SSL with s.TLSConfig) with local_hostname, starttls(context) for
// Encryption "TLS", then login only when both user and password are set.
// When a step after the connection fails, __enter__'s close() sends QUIT
// (impossible after a failed TLS handshake, whose plain socket Python has
// detached); a failed connection or greeting sends nothing more.
func OpenBackend(ctx context.Context, s Server) (*Backend, error) {
	encryption := s.Encryption
	if encryption == "TLS" {
		s.Encryption = ""
	}
	c, err := open(ctx, s)
	if err != nil {
		return nil, err
	}
	b := &Backend{c: c}
	if encryption == "TLS" {
		if err = c.starttls(); err != nil {
			b.Close()
			return nil, err
		}
	}
	if s.User != "" && s.Password != "" {
		if err = c.login(s.User, s.Password); err != nil {
			b.Close()
			return nil, err
		}
	}
	return b, nil
}

// Close is EmailBackend.close(): quit(), where a disconnection (or a socket
// Python no longer holds) only closes the connection.
func (b *Backend) Close() {
	if b.c == nil {
		return
	}
	if !b.c.handshake {
		if err := b.c.putcmd("quit", ""); err == nil {
			_, _, _ = b.c.getreply()
		}
	}
	b.c.close()
	b.c = nil
}

// SendMail is the SMTP backend's _send: sanitize_address of the From value
// and of every recipient, then sendmail(from, recipients,
// message.as_bytes(linesep='\r\n')).
func (b *Backend) SendMail(from string, to []string, message []byte) error {
	sender, err := sanitizeAddress(from)
	if err != nil {
		return err
	}
	recipients := make([]string, len(to))
	for i, address := range to {
		if recipients[i], err = sanitizeAddress(address); err != nil {
			return err
		}
	}
	return b.c.sendmailFrom(sender, sender, recipients, message)
}

// SendDjangoTestMail is the SMTP backend's _send for the admin test message:
// from is the formataddr value the probe passes to EmailMessage and recipient
// is an EmailField value. Django's SMTP backend runs both through
// sanitize_address and then smtplib.quoteaddr.
func (b *Backend) SendDjangoTestMail(from, recipient string, message []byte) error {
	sender, envelope, err := djangoTestAddresses(from, recipient)
	if err != nil {
		return err
	}
	return b.c.sendmailFrom(sender, sender, []string{envelope}, message)
}

// djangoTestAddresses is the SMTP backend's sanitize_address for the
// formataddr sender and for the recipient.
func djangoTestAddresses(from, recipient string) (sender, envelope string, err error) {
	if sender, err = sanitizeAddress(from); err != nil {
		return "", "", err
	}
	if envelope, err = sanitizeAddress(recipient); err != nil {
		return "", "", err
	}
	return sender, envelope, nil
}

// DjangoTestEnvelope returns the text after "mail from:" and "rcpt to:":
// smtplib.quoteaddr of the sanitized sender and recipient.
func DjangoTestEnvelope(from, recipient string) (mailFrom, rcptTo string, err error) {
	sender, envelope, err := djangoTestAddresses(from, recipient)
	if err != nil {
		return "", "", err
	}
	return QuoteAddr(sender), QuoteAddr(envelope), nil
}

// DjangoText is an EmailMessage(subject, body, formataddr((sender_name,
// from_email)), [to]) with a text/plain utf-8 body, as Django 5.2 renders
// message().as_bytes(linesep='\r\n') under the compat32 policy.
type DjangoText struct {
	Subject    string
	Body       string
	SenderName string
	FromEmail  string
	To         string
	Date       time.Time
	// MessageID is make_msgid(domain=DNS_NAME).
	MessageID string
}

// MessageID is email.utils.make_msgid(domain=domain) at now.
func MessageID(now time.Time, domain string) string {
	return fmt.Sprintf("<%d.%d.%d@%s>", now.UnixNano()/1e7, os.Getpid(), rand.Uint64(), domain)
}

// RenderDjangoText renders the message: ErrFormatAddr when formataddr raises
// (a non-ASCII from address), an error matching ErrRaises for the exceptions
// Django raises while it builds or folds the message (BadHeaderError,
// ValueError from sanitize_address, UnicodeEncodeError).
func RenderDjangoText(m DjangoText) ([]byte, error) {
	from, err := FormatAddr(m.SenderName, m.FromEmail)
	if err != nil {
		return nil, err
	}
	return renderDjangoMessage(DjangoMessage{Subject: m.Subject, Body: m.Body, From: from, To: []string{m.To},
		Date: m.Date, MessageID: m.MessageID})
}

var (
	addressSpecialsRe = regexp.MustCompile(`[\]\[\\()<>@,:;".]`)
	addressEscapes    = regexp.MustCompile(`[\\"]`)
)

// ErrFormatAddr is the UnicodeEncodeError email.utils.formataddr raises: it
// encodes the address, not the display name, as ASCII.
var ErrFormatAddr = errors.New("smtpsend: formataddr raises UnicodeEncodeError")

// FormatAddr is email.utils.formataddr((name, address)) with charset
// utf-8: a non-ASCII name becomes one encoded word (the shorter of base64
// and Q, Q on a tie), an ASCII name with specials is quoted. A non-ASCII
// address makes formataddr raise (ErrFormatAddr).
func FormatAddr(name, address string) (string, error) {
	if !isASCII(address) {
		return "", ErrFormatAddr
	}
	if name == "" {
		return address, nil
	}
	if !isASCII(name) {
		return HeaderEncode(name) + " <" + address + ">", nil
	}
	quotes := ""
	if addressSpecialsRe.MatchString(name) {
		quotes = `"`
	}
	name = addressEscapes.ReplaceAllString(name, `\$0`)
	return quotes + name + quotes + " <" + address + ">", nil
}

// SanitizeAddress is Django's sanitize_address(address, 'utf-8') for one
// address string: the mailbox parser splits the display name, the local part
// and the domain, a non-ASCII display name and local part become RFC 2047
// words and the domain goes through str.encode('idna') (IDNA 2003).
func SanitizeAddress(address string) (string, error) { return sanitizeAddress(address) }

// HeaderEncode is Charset('utf-8').header_encode(text).
func HeaderEncode(text string) string {
	raw := []byte(text)
	b64 := (len(raw) + 2) / 3 * 4
	var q strings.Builder
	for _, b := range raw {
		switch {
		case b == ' ':
			q.WriteByte('_')
		case b >= 'a' && b <= 'z', b >= 'A' && b <= 'Z', b >= '0' && b <= '9', strings.IndexByte("-!*+/", b) >= 0:
			q.WriteByte(b)
		default:
			fmt.Fprintf(&q, "=%02X", b)
		}
	}
	if b64 < q.Len() {
		return "=?utf-8?b?" + base64.StdEncoding.EncodeToString(raw) + "?="
	}
	return "=?utf-8?q?" + q.String() + "?="
}

type foldChunk struct{ fws, part string }

var foldWhitespace = regexp.MustCompile(`[ \t]+`)
