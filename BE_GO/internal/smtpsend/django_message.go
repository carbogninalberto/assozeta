package smtpsend

import (
	"context"
	"strings"
	"time"
	"unicode/utf8"
)

// DjangoMessage is an EmailMultiAlternatives(subject, body, from_email, to,
// reply_to=reply_to) with an optional text/html alternative, as Django 5.2
// renders message().as_bytes(linesep='\r\n') under the compat32 policy
// (core.tasks.custom_send_mail). Values are the raw constructor arguments.
type DjangoMessage struct {
	Subject string
	// Body is the text/plain body (None and "" are both "": the part is
	// omitted from the alternative when empty).
	Body string
	// HTML, when non-empty, is attached as the text/html alternative.
	HTML      string
	From      string
	To        []string
	ReplyTo   []string
	Date      time.Time
	MessageID string
	// Boundary is Generator._make_boundary's random base (NewBoundary); the
	// collision suffix is applied as Python does.
	Boundary string
}

// rfc5322LineLimit is RFC5322_EMAIL_LINE_LENGTH_LIMIT: a longer body line
// makes Django switch the part to quoted-printable (outside the subset).
const rfc5322LineLimit = 998

// DjangoEnvelope returns the SMTP envelope sender of a supported From value
// (sanitize_address followed by smtplib.quoteaddr).
func DjangoEnvelope(from string) (string, error) {
	_, address, err := djangoFrom(from)
	return address, err
}

// djangoFrom splits a From value of the subset: "address" or
// "display name <address>" with a plain address and a display name made of
// letters, digits, spaces and the characters email.utils accepts unquoted.
func djangoFrom(from string) (name, address string, err error) {
	if !strings.HasSuffix(from, ">") {
		if !plainAddress(from) || !strings.Contains(from, "@") || !isASCII(from) {
			return "", "", ErrUnsupported
		}
		return "", from, nil
	}
	open := strings.LastIndex(from, " <")
	if open <= 0 {
		return "", "", ErrUnsupported
	}
	name, address = from[:open], from[open+2:len(from)-1]
	if !plainAddress(address) || !strings.Contains(address, "@") || !isASCII(address) {
		return "", "", ErrUnsupported
	}
	if strings.TrimSpace(name) != name || strings.Contains(name, "  ") || !utf8.ValidString(name) {
		return "", "", ErrUnsupported
	}
	for _, r := range name {
		switch {
		case r == ' ', r == '-', r == '\'', r == '&', r == '!', r == '/', r == '+':
		case r < 0x80 && (r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9'):
		case r >= 0x80 && r != 0x85 && r != 0x2028 && r != 0x2029 && r != 0xa0 && !(r >= 0x2000 && r <= 0x200a):
		default:
			return "", "", ErrUnsupported
		}
	}
	return name, address, nil
}

// plainHeaderAddresses checks To/Reply-To entries: ASCII plain addresses
// (str(v) joined with ", " and kept as is by forbid_multi_line_headers).
func plainHeaderAddresses(addresses []string) bool {
	for _, address := range addresses {
		if !isASCII(address) || !plainAddress(address) || !strings.Contains(address, "@") {
			return false
		}
	}
	return true
}

// subjectValue is forbid_multi_line_headers("Subject", ...) for the subset:
// ASCII text that Header(val).encode() keeps on one line (at most 76
// characters, no folding whitespace runs) and non-ASCII text that
// Header(val, 'utf-8').encode() writes as one encoded word.
func subjectValue(subject string) (string, error) {
	if !utf8.ValidString(subject) || hasLineBreak(subject) {
		return "", ErrUnsupported
	}
	if isASCII(subject) {
		if len(subject) > 76 || strings.ContainsAny(subject, "\t") || strings.Contains(subject, "  ") ||
			strings.HasPrefix(subject, " ") || strings.HasSuffix(subject, " ") {
			return "", ErrUnsupported
		}
		for i := 0; i < len(subject); i++ {
			if subject[i] < 0x20 || subject[i] == 0x7f {
				return "", ErrUnsupported
			}
		}
		return subject, nil
	}
	encoded := HeaderEncode(subject)
	if len(encoded) > 76 {
		return "", ErrUnsupported
	}
	return encoded, nil
}

// fromValue is forbid_multi_line_headers("From", ...): an ASCII value is
// kept as given; otherwise sanitize_address re-encodes the display name.
func fromValue(from string) (string, error) {
	name, address, err := djangoFrom(from)
	if err != nil {
		return "", err
	}
	if isASCII(from) {
		return from, nil
	}
	encoded := HeaderEncode(name)
	if len(encoded) > 76 {
		return "", ErrUnsupported
	}
	return encoded + " <" + address + ">", nil
}

// mimeTextPart is SafeMIMEText(text, subtype, 'utf-8') flattened by the
// BytesGenerator: 7bit for ASCII text, 8bit otherwise; NLCRE line breaks
// become CRLF. ErrUnsupported for a line over 998 bytes (quoted-printable).
func mimeTextPart(text, subtype string) (string, error) {
	if !utf8.ValidString(text) {
		return "", ErrUnsupported
	}
	for _, line := range strings.FieldsFunc(text, func(r rune) bool { return r == '\n' || r == '\r' }) {
		if len(line) > rfc5322LineLimit {
			return "", ErrUnsupported
		}
	}
	encoding := "8bit"
	if isASCII(text) {
		encoding = "7bit"
	}
	return "Content-Type: text/" + subtype + "; charset=\"utf-8\"\r\nMIME-Version: 1.0\r\nContent-Transfer-Encoding: " +
		encoding + "\r\n\r\n" + eolPattern.ReplaceAllString(text, "\r\n"), nil
}

// CheckDjangoMessage reports whether RenderDjangoMessage supports m (the
// date, Message-ID and boundary do not matter).
func CheckDjangoMessage(m DjangoMessage) error {
	m.Date, m.MessageID, m.Boundary = time.Unix(0, 0), "<0.0.0@localhost>", NewBoundary()
	_, err := RenderDjangoMessage(m)
	return err
}

// RenderDjangoMessage returns the bytes Django's SMTP backend sends.
func RenderDjangoMessage(m DjangoMessage) ([]byte, error) {
	if len(m.To) == 0 || !plainHeaderAddresses(m.To) || !plainHeaderAddresses(m.ReplyTo) {
		return nil, ErrUnsupported
	}
	subject, err := subjectValue(m.Subject)
	if err != nil {
		return nil, err
	}
	from, err := fromValue(m.From)
	if err != nil {
		return nil, err
	}
	if !isASCII(m.MessageID) || hasLineBreak(m.MessageID) {
		return nil, ErrUnsupported
	}
	var out strings.Builder
	header := func(name, value string) {
		out.WriteString(name + ": " + FoldASCII(len(name)+2, value, 78, "\r\n") + "\r\n")
	}
	tail := func() {
		header("Subject", subject)
		header("From", from)
		header("To", strings.Join(m.To, ", "))
		if len(m.ReplyTo) > 0 {
			header("Reply-To", strings.Join(m.ReplyTo, ", "))
		}
		header("Date", m.Date.UTC().Format("Mon, 02 Jan 2006 15:04:05 -0000"))
		header("Message-ID", m.MessageID)
	}
	text, err := mimeTextPart(m.Body, "plain")
	if err != nil {
		return nil, err
	}
	if m.HTML == "" {
		// The single part carries the message headers after its own.
		head, body, _ := strings.Cut(text, "\r\n\r\n")
		out.WriteString(head + "\r\n")
		tail()
		out.WriteString("\r\n" + body)
		return []byte(out.String()), nil
	}
	html, err := mimeTextPart(m.HTML, "html")
	if err != nil {
		return nil, err
	}
	parts := []string{html}
	if m.Body != "" {
		parts = []string{text, html}
	}
	boundary := uniqueBoundary(m.Boundary, strings.Join(parts, "\r\n"))
	header("Content-Type", `multipart/alternative; boundary="`+boundary+`"`)
	header("MIME-Version", "1.0")
	tail()
	out.WriteString("\r\n")
	for _, part := range parts {
		out.WriteString("--" + boundary + "\r\n" + part + "\r\n")
	}
	out.WriteString("--" + boundary + "--\r\n")
	return []byte(out.String()), nil
}

// OpenDjangoBackend is EmailBackend.open() as send_messages calls it: the
// connection (SMTP or SMTP_SSL with s.TLSConfig), starttls for "TLS", then
// login when both user and password are set. A failure after connecting
// raises without quit(): the socket is released when the object is.
func OpenDjangoBackend(ctx context.Context, s Server) (*Backend, error) {
	encryption := s.Encryption
	if encryption == "TLS" {
		s.Encryption = ""
	}
	c, err := open(ctx, s)
	if err != nil {
		return nil, err
	}
	if encryption == "TLS" {
		if err = c.starttls(); err != nil {
			c.close()
			return nil, err
		}
	}
	if s.User != "" && s.Password != "" {
		if err = c.login(s.User, s.Password); err != nil {
			c.close()
			return nil, err
		}
	}
	return &Backend{c: c}, nil
}
