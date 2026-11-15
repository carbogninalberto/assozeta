package smtpsend

import (
	"context"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
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
// makes Django switch the part to quoted-printable.
const rfc5322LineLimit = 998

// DjangoEnvelope returns the From address Django's SMTP backend passes to
// smtplib.sendmail: sanitize_address(value, 'utf-8'). ErrRaises when Python
// raises while sanitizing it.
func DjangoEnvelope(from string) (string, error) { return sanitizeAddress(from) }

// CheckDjangoMessage reports whether RenderDjangoMessage renders m (the date,
// Message-ID and boundary do not matter).
func CheckDjangoMessage(m DjangoMessage) error {
	m.Date, m.MessageID, m.Boundary = time.Unix(0, 0), "<0.0.0@localhost>", NewBoundary()
	_, err := RenderDjangoMessage(m)
	return err
}

// RenderDjangoMessage returns the bytes Django's SMTP backend sends.
func RenderDjangoMessage(m DjangoMessage) ([]byte, error) {
	return renderDjangoMessage(m)
}

// renderDjangoMessage is EmailMessage.message().as_bytes(linesep='\r\n'): the
// text part (and the alternative), then the headers forbid_multi_line_headers
// produces, each folded the way compat32 folds it.
func renderDjangoMessage(m DjangoMessage) ([]byte, error) {
	if m.From == "" {
		// EmailMessage.__init__ replaces a falsy from_email with
		// settings.DEFAULT_FROM_EMAIL, which only the environment carries;
		// every caller resolves it first (instance_outbound.go:281 answers
		// not_configured for an empty sender, send_mail_async.go:277 hands
		// off a From that is not an address).
		return nil, refusal.AppState("smtpsend: empty From (Django uses DEFAULT_FROM_EMAIL)")
	}
	text, err := djangoTextPart(m.Body, "plain")
	if err != nil {
		return nil, err
	}
	var (
		parts    []string
		boundary string
	)
	if m.HTML != "" {
		html, err := djangoTextPart(m.HTML, "html")
		if err != nil {
			return nil, err
		}
		htmlText, err := html.message()
		if err != nil {
			return nil, err
		}
		parts = []string{htmlText}
		if m.Body != "" {
			textText, err := text.message()
			if err != nil {
				return nil, err
			}
			parts = []string{textText, htmlText}
		}
		boundary = uniqueBoundary(m.Boundary, strings.Join(parts, "\r\n"))
	}
	var out strings.Builder
	header := func(name, value string) error {
		line, err := djangoHeaderLine(name, value)
		if err != nil {
			return err
		}
		out.WriteString(line)
		return nil
	}
	if boundary != "" {
		if err := header("Content-Type", `multipart/alternative; boundary="`+boundary+`"`); err != nil {
			return nil, err
		}
		if err := header("MIME-Version", "1.0"); err != nil {
			return nil, err
		}
	} else {
		head, err := text.headers()
		if err != nil {
			return nil, err
		}
		out.WriteString(head)
	}
	if err := header("Subject", m.Subject); err != nil {
		return nil, err
	}
	if err := header("From", m.From); err != nil {
		return nil, err
	}
	if len(m.To) > 0 {
		if err := header("To", strings.Join(m.To, ", ")); err != nil {
			return nil, err
		}
	}
	if len(m.ReplyTo) > 0 {
		if err := header("Reply-To", strings.Join(m.ReplyTo, ", ")); err != nil {
			return nil, err
		}
	}
	if err := header("Date", m.Date.UTC().Format("Mon, 02 Jan 2006 15:04:05 -0000")); err != nil {
		return nil, err
	}
	if err := header("Message-ID", m.MessageID); err != nil {
		return nil, err
	}
	if boundary == "" {
		out.WriteString("\r\n")
		out.WriteString(text.payload)
		return []byte(out.String()), nil
	}
	out.WriteString("\r\n")
	for _, part := range parts {
		out.WriteString("--" + boundary + "\r\n" + part + "\r\n")
	}
	out.WriteString("--" + boundary + "--\r\n")
	return []byte(out.String()), nil
}

// djangoPart is one SafeMIMEText(text, subtype, 'utf-8') as the BytesGenerator
// flattens it: its three MIME headers and the transfer-encoded payload.
type djangoPart struct {
	contentType string
	cte         string
	// payload holds the bytes of the part body, its line endings already
	// normalized to CRLF.
	payload string
}

// djangoMIMEHeaders are the headers MIMEText and set_charset add, in order.
func (p djangoPart) mimeHeaders() [][2]string {
	return [][2]string{
		{"Content-Type", p.contentType},
		{"MIME-Version", "1.0"},
		{"Content-Transfer-Encoding", p.cte},
	}
}

// headers is the part's own header block, each header folded and followed by
// CRLF.
func (p djangoPart) headers() (string, error) {
	var out strings.Builder
	for _, header := range p.mimeHeaders() {
		line, err := djangoHeaderLine(header[0], header[1])
		if err != nil {
			return "", err
		}
		out.WriteString(line)
	}
	return out.String(), nil
}

// message is the flattened subpart: its headers, the blank line and the body.
func (p djangoPart) message() (string, error) {
	head, err := p.headers()
	if err != nil {
		return "", err
	}
	return head + "\r\n" + p.payload, nil
}

// djangoTextPart is SafeMIMEText(text, subtype, 'utf-8'): the part with the
// transfer encoding Django picks for it. A line over the RFC 5322 limit makes
// set_payload use the quoted-printable charset (#22561), which shortens it;
// otherwise the text is 7bit or 8bit.
func djangoTextPart(text, subtype string) (djangoPart, error) {
	part := djangoPart{contentType: "text/" + subtype + `; charset="utf-8"`}
	raw, err := djangoTextBytes(text)
	if err != nil {
		return part, err
	}
	long, err := djangoHasLongLines(text)
	if err != nil {
		return part, err
	}
	if long {
		part.cte = "quoted-printable"
		part.payload = djangoEOLs(qpBodyEncode(raw))
		return part, nil
	}
	part.cte = "7bit"
	if !isASCIIBytes(raw) {
		part.cte = "8bit"
	}
	part.payload = djangoEOLs(string(raw))
	return part, nil
}

// djangoTextBytes is Message.set_payload's text.encode('utf-8',
// 'surrogateescape') for the text Django hands a SafeMIMEText: the UTF-8 bytes
// of the text, with a lone surrogate U+DC80..U+DCFF carrying the byte it
// escapes. Any other surrogate raises UnicodeEncodeError.
func djangoTextBytes(text string) ([]byte, error) {
	runes, ok := pythonRunes(text)
	if !ok {
		return nil, errNotPythonStr
	}
	return bodyTextBytes(runes)
}

// djangoHasLongLines is SafeMIMEText.set_payload's has_long_lines: a line of
// the text (str.splitlines) whose UTF-8 bytes exceed the RFC 5322 limit.
func djangoHasLongLines(text string) (bool, error) {
	for _, line := range pySplitlines(text) {
		runes, ok := pythonRunes(line)
		if !ok {
			return false, errNotPythonStr
		}
		raw, err := bodyTextBytes(runes)
		if err != nil {
			return false, err
		}
		if len(raw) > rfc5322LineLimit {
			return true, nil
		}
	}
	return false, nil
}

// isASCIIBytes reports whether every byte is ASCII.
func isASCIIBytes(raw []byte) bool {
	for _, b := range raw {
		if b >= 0x80 {
			return false
		}
	}
	return true
}

// djangoEOLs is the generator's _write_lines: \r\n, \n and a lone \r become
// the linesep, \r\n.
func djangoEOLs(text string) string {
	if !strings.ContainsAny(text, "\r\n") {
		return text
	}
	var out strings.Builder
	out.Grow(len(text) + 8)
	for i := 0; i < len(text); i++ {
		switch text[i] {
		case '\r':
			if i+1 < len(text) && text[i+1] == '\n' {
				i++
			}
			out.WriteString("\r\n")
		case '\n':
			out.WriteString("\r\n")
		default:
			out.WriteByte(text[i])
		}
	}
	return out.String()
}

// qpBodySafe reports the octets email.quoprimime leaves unencoded in a body.
var qpBodySafe = func() [256]bool {
	var safe [256]bool
	for _, c := range []byte(" !\"#$%&'()*+,-./0123456789:;<>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`" +
		"abcdefghijklmnopqrstuvwxyz{|}~\t") {
		safe[c] = true
	}
	return safe
}()

const upperHex = "0123456789ABCDEF"

// qpBodyEncode is email.quoprimime.body_encode(body, maxlinelen=76, eol='\n')
// for the bytes of the text.
func qpBodyEncode(raw []byte) string {
	if len(raw) == 0 {
		return ""
	}
	encoded := make([]byte, 0, len(raw)+len(raw)/3)
	for _, b := range raw {
		if b == '\r' || b == '\n' || qpBodySafe[b] {
			encoded = append(encoded, b)
			continue
		}
		encoded = append(encoded, '=', upperHex[b>>4], upperHex[b&0x0f])
	}
	var lines []string
	for _, line := range splitQPLines(encoded) {
		lines = append(lines, qpEncodeLine(line)...)
	}
	// Add back the final newline when the body ends with one.
	if last := encoded[len(encoded)-1]; last == '\r' || last == '\n' {
		lines = append(lines, "")
	}
	return strings.Join(lines, "\n")
}

// splitQPLines is str.splitlines() of the translated body: it splits on \r and
// \n (every other line-break character is quoted) and drops the empty text
// after a final line break.
func splitQPLines(body []byte) [][]byte {
	var lines [][]byte
	start := 0
	for i := 0; i < len(body); i++ {
		if body[i] != '\r' && body[i] != '\n' {
			continue
		}
		lines = append(lines, body[start:i])
		if body[i] == '\r' && i+1 < len(body) && body[i+1] == '\n' {
			i++
		}
		start = i + 1
	}
	if start < len(body) {
		lines = append(lines, body[start:])
	}
	return lines
}

// qpEncodeLine wraps one translated line into pieces of at most 76 octets,
// each hard piece followed by the soft line break '=', and encodes trailing
// whitespace so no line ends with it.
func qpEncodeLine(line []byte) []string {
	const (
		maxLineLen  = 76
		maxLineLen1 = maxLineLen - 1
	)
	var out []string
	start := 0
	lastStart := len(line) - 1 - maxLineLen
	for start <= lastStart {
		stop := start + maxLineLen1
		switch {
		case line[stop-2] == '=':
			out = append(out, string(line[start:stop-1]))
			start = stop - 2
		case line[stop-1] == '=':
			out = append(out, string(line[start:stop]))
			start = stop - 1
		default:
			out = append(out, string(line[start:stop])+"=")
			start = stop
		}
	}
	if len(line) > 0 && (line[len(line)-1] == ' ' || line[len(line)-1] == '\t') {
		room := start - lastStart
		var quoted string
		switch {
		case room >= 3:
			quoted = qpQuoteByte(line[len(line)-1])
		case room == 2:
			quoted = string(line[len(line)-1]) + "=\n"
		default:
			quoted = "=\n" + qpQuoteByte(line[len(line)-1])
		}
		out = append(out, string(line[start:len(line)-1])+quoted)
		return out
	}
	out = append(out, string(line[start:]))
	return out
}

// qpQuoteByte is quoprimime.quote.
func qpQuoteByte(b byte) string {
	return string([]byte{'=', upperHex[b>>4], upperHex[b&0x0f]})
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
