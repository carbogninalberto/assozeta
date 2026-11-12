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

	"github.com/carbogninalberto/assozeta/BE_GO/internal/idna2003"
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

// SendMail is the SMTP backend's _send: sendmail(from, recipients,
// message.as_bytes(linesep='\r\n')). from and to must pass CheckEnvelope
// (sanitize_address and quoteaddr leave them unchanged).
func (b *Backend) SendMail(from string, to []string, message []byte) error {
	if err := CheckEnvelope(from, to); err != nil {
		return err
	}
	return b.c.sendmailBytes(from, to, message)
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
	b.c.anyAddress = true
	return b.c.sendmailFrom(sender, sender, []string{envelope}, message)
}

// djangoTestAddresses is the SMTP backend's sanitize_address for the
// formataddr sender and the recipient. The sender is the formataddr value
// itself: for the ASCII values that reach a send, Django's sanitize_address
// keeps the display name and address, and smtplib.quoteaddr reads the address
// part (or the whole value when a domain literal stops its parse).
func djangoTestAddresses(from, recipient string) (sender, envelope string, err error) {
	if envelope, err = SanitizeAddress(recipient); err != nil {
		return "", "", err
	}
	return from, envelope, nil
}

// DjangoTestEnvelope returns the text after "mail from:" and "rcpt to:":
// smtplib.quoteaddr of the formataddr sender and of
// sanitize_address(recipient).
func DjangoTestEnvelope(from, recipient string) (mailFrom, rcptTo string, err error) {
	sender, envelope, err := djangoTestAddresses(from, recipient)
	if err != nil {
		return "", "", err
	}
	return QuoteAddr(sender), QuoteAddr(envelope), nil
}

// DjangoText is an EmailMessage(subject, body, from_email, [to]) with a
// text/plain utf-8 body, as Django 5.2 renders message().as_bytes(
// linesep='\r\n') under the compat32 policy.
type DjangoText struct {
	// EncodedSubject is the Subject value after forbid_multi_line_headers.
	EncodedSubject string
	Body           string
	SenderName     string
	FromEmail      string
	To             string
	Date           time.Time
	// MessageID is make_msgid(domain=DNS_NAME).
	MessageID string
}

// MessageID is email.utils.make_msgid(domain=domain) at now.
func MessageID(now time.Time, domain string) string {
	return fmt.Sprintf("<%d.%d.%d@%s>", now.UnixNano()/1e7, os.Getpid(), rand.Uint64(), domain)
}

var headerLine = regexp.MustCompile(`[\r\n]`)

// RenderDjangoText renders the message; ErrFormatAddr when formataddr raises
// (a non-ASCII from address), ErrUnsupported for a line break in a header or
// a body line over 998 bytes (which Django sends quoted-printable).
func RenderDjangoText(m DjangoText) ([]byte, error) {
	from, err := FormatAddr(m.SenderName, m.FromEmail)
	if err != nil {
		return nil, err
	}
	to := m.To
	if !isASCII(to) {
		if to, err = sanitizeHeaderAddress(to); err != nil {
			return nil, err
		}
	}
	for _, value := range []string{m.EncodedSubject, from, to, m.MessageID} {
		if !isASCII(value) || headerLine.MatchString(value) {
			return nil, ErrUnsupported
		}
	}
	for _, line := range strings.FieldsFunc(m.Body, func(r rune) bool { return r == '\n' || r == '\r' }) {
		if len(line) > 998 {
			return nil, ErrUnsupported
		}
	}
	if strings.ContainsAny(m.Body, "\r\n") {
		return nil, ErrUnsupported
	}
	var out strings.Builder
	header := func(name, value string) {
		out.WriteString(name + ": " + FoldASCII(len(name)+2, value, 78, "\r\n") + "\r\n")
	}
	header("Content-Type", `text/plain; charset="utf-8"`)
	header("MIME-Version", "1.0")
	header("Content-Transfer-Encoding", "8bit")
	header("Subject", m.EncodedSubject)
	header("From", from)
	header("To", to)
	header("Date", m.Date.UTC().Format("Mon, 02 Jan 2006 15:04:05 -0000"))
	header("Message-ID", m.MessageID)
	out.WriteString("\r\n")
	out.WriteString(m.Body)
	return []byte(out.String()), nil
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

// SanitizeAddress is Django's sanitize_address(address, encoding) for one
// address string (the SMTP envelope): the parser removes the quoting of a
// quoted local part, a non-ASCII local part becomes one RFC 2047 word and the
// domain goes through str.encode('idna') (IDNA 2003). Address's addr_spec
// re-quotes the local part.
func SanitizeAddress(address string) (string, error) {
	local, domain, ok := cutAddress(address)
	if !ok {
		return "", ErrUnsupported
	}
	if len(local) >= 2 && local[0] == '"' && local[len(local)-1] == '"' {
		local = unquoteLocal(local[1 : len(local)-1])
	}
	return sanitizeAddressParts(local, domain)
}

// sanitizeHeaderAddress is the tuple branch of sanitize_address that
// forbid_multi_line_headers uses for a non-ASCII To value: getaddresses keeps
// the address as written, so the local part is not unquoted.
func sanitizeHeaderAddress(address string) (string, error) {
	local, domain, ok := cutAddress(address)
	if !ok {
		return "", ErrUnsupported
	}
	return sanitizeAddressParts(local, domain)
}

func cutAddress(address string) (local, domain string, ok bool) {
	at := strings.LastIndexByte(address, '@')
	if at < 0 || at == len(address)-1 {
		return "", "", false
	}
	return address[:at], address[at+1:], true
}

func sanitizeAddressParts(local, domain string) (string, error) {
	if !isASCII(local) {
		local = HeaderEncode(local)
		// Header(local, 'utf-8').encode() folds past 78 characters; Address
		// then rejects the line break, as Django's sanitize_address does.
		if len(local) > 78 {
			return "", ErrUnsupported
		}
	}
	encoded, err := idna2003.ToASCII(domain)
	if err != nil {
		return "", ErrUnsupported
	}
	return addressSpec(local, encoded), nil
}

// addressSpec is headerregistry.Address(username=local, domain=domain)
// .addr_spec: the local part is quoted when it contains a DOT_ATOM_ENDS
// character.
func addressSpec(local, domain string) string {
	if strings.ContainsAny(local, "()<>@,:;\"[] \\\t") {
		local = `"` + strings.NewReplacer(`\`, `\\`, `"`, `\"`).Replace(local) + `"`
	}
	return local + "@" + domain
}

// unquoteLocal removes the quoted-pair escapes of a quoted local part.
func unquoteLocal(local string) string {
	var out strings.Builder
	for i := 0; i < len(local); i++ {
		if local[i] == '\\' && i+1 < len(local) {
			i++
		}
		out.WriteByte(local[i])
	}
	return out.String()
}

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

type foldLine struct {
	parts   []foldChunk
	initial int
}

func (l *foldLine) size() int {
	n := l.initial
	for _, c := range l.parts {
		n += len(c.fws) + len(c.part)
	}
	return n
}

func (l *foldLine) String() string {
	var out strings.Builder
	for _, c := range l.parts {
		out.WriteString(c.fws + c.part)
	}
	return out.String()
}

func (l *foldLine) pop() foldChunk {
	if len(l.parts) == 0 {
		return foldChunk{}
	}
	last := l.parts[len(l.parts)-1]
	l.parts = l.parts[:len(l.parts)-1]
	return last
}

func (l *foldLine) onlyWhitespace() bool {
	return l.initial == 0 && (len(l.parts) == 0 || strings.TrimSpace(l.String()) == "" && l.String() != "")
}

var foldWhitespace = regexp.MustCompile(`[ \t]+`)

// FoldASCII is compat32's Header(value, header_name=name).encode(linesep,
// maxlinelen) for a single-line us-ascii value (headerlen is len(name)+2):
// email.header._ValueFormatter with splitchars ';, \t'.
func FoldASCII(headerlen int, value string, maxlen int, linesep string) string {
	var lines []string
	current := &foldLine{initial: headerlen}
	newline := func() {
		end := current.pop()
		if end != (foldChunk{fws: " "}) {
			current.parts = append(current.parts, end)
		}
		if current.size() > 0 {
			if current.onlyWhitespace() && len(lines) > 0 {
				lines[len(lines)-1] += current.String()
			} else {
				lines = append(lines, current.String())
			}
		}
		current = &foldLine{}
	}
	appendChunk := func(fws, part string) {
		current.parts = append(current.parts, foldChunk{fws, part})
		if current.size() <= maxlen {
			return
		}
		split := -1
		for _, ch := range []byte(";, \t") {
			for i := len(current.parts) - 1; i > 0; i-- {
				if ch == ' ' || ch == '\t' {
					if f := current.parts[i].fws; f != "" && f[0] == ch {
						split = i
						break
					}
				}
				if prev := current.parts[i-1].part; prev != "" && prev[len(prev)-1] == ch {
					split = i
					break
				}
			}
			if split >= 0 {
				break
			}
		}
		if split < 0 {
			last := current.pop()
			if current.initial > 0 {
				newline()
				if last.fws == "" {
					last.fws = " "
				}
			}
			current.parts = append(current.parts, last)
			return
		}
		remainder := append([]foldChunk(nil), current.parts[split:]...)
		current.parts = current.parts[:split]
		lines = append(lines, current.String())
		current = &foldLine{parts: remainder}
	}
	// re.split("([ \t]+)", value) as (fws, part) pairs.
	var fwsList, partList []string
	last := 0
	for _, loc := range foldWhitespace.FindAllStringIndex(value, -1) {
		partList = append(partList, value[last:loc[0]])
		fwsList = append(fwsList, value[loc[0]:loc[1]])
		last = loc[1]
	}
	partList = append(partList, value[last:])
	pairs := []foldChunk{}
	if partList[0] != "" {
		pairs = append(pairs, foldChunk{"", partList[0]})
	}
	for i, fws := range fwsList {
		pairs = append(pairs, foldChunk{fws, partList[i+1]})
	}
	for _, p := range pairs {
		appendChunk(p.fws, p.part)
	}
	current.parts = append(current.parts, foldChunk{fws: " "}) // add_transition
	newline()
	return strings.Join(lines, linesep)
}
