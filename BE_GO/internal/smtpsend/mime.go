package smtpsend

import (
	"encoding/base64"
	"fmt"
	"math"
	"math/rand/v2"
	"regexp"
	"strings"
	"unicode"
	"unicode/utf8"
)

// Message holds the arguments send_email turns into its MIMEMultipart.
type Message struct {
	SenderName string // email_sender_name; empty (or None) means From is the user alone
	SenderUser string // email_smtp_user
	To         []string
	Subject    string
	ReplyTo    []string // empty means no Reply-To header
	HTML       string   // non-empty: multipart/alternative with a text/html part
	Text       string   // otherwise multipart/mixed with a text/plain part
}

// maxHeaderRunes keeps every header far below the 1,000,000 character line
// that as_string(maxheaderlen=0) gives Header.encode, so no value is split.
const maxHeaderRunes = 4096

// Render returns msg.as_string() for the MIMEMultipart send_email builds,
// with boundary as the generated boundary (Python's random base; the
// collision suffix is applied here as _make_boundary does).
func Render(m Message, boundary string) (string, error) {
	from := m.SenderUser
	if m.SenderName != "" {
		from = m.SenderName + " <" + m.SenderUser + ">"
	}
	headers := [][2]string{{"From", from}, {"To", strings.Join(m.To, ", ")}, {"Subject", m.Subject}}
	if len(m.ReplyTo) > 0 {
		headers = append(headers, [2]string{"Reply-To", strings.Join(m.ReplyTo, ", ")})
	}
	subtype, body, content := "mixed", m.Text, "plain"
	if m.HTML != "" {
		subtype, body, content = "alternative", m.HTML, "html"
	}
	if !utf8.ValidString(body) {
		return "", ErrUnsupported
	}
	var folded []string
	for _, header := range headers {
		value, err := headerValue(header[1])
		if err != nil {
			return "", err
		}
		folded = append(folded, header[0]+": "+value+"\n")
	}
	part := "Content-Type: text/" + content + "; charset=\"utf-8\"\nMIME-Version: 1.0\n" +
		"Content-Transfer-Encoding: base64\n\n" + bodyBase64([]byte(body))
	boundary = uniqueBoundary(boundary, part)
	var out strings.Builder
	out.WriteString("Content-Type: multipart/" + subtype + "; boundary=\"" + boundary + "\"\nMIME-Version: 1.0\n")
	for _, line := range folded {
		out.WriteString(line)
	}
	out.WriteString("\n--" + boundary + "\n" + part + "\n--" + boundary + "--\n")
	return out.String(), nil
}

// NewBoundary is Generator._make_boundary's random base:
// '=' * 15 + random.randrange(sys.maxsize) as 19 digits + '=='.
func NewBoundary() string {
	return strings.Repeat("=", 15) + fmt.Sprintf("%019d", rand.Int64N(math.MaxInt64)) + "=="
}

func uniqueBoundary(boundary, text string) string {
	candidate := boundary
	for counter := 0; ; counter++ {
		pattern := regexp.MustCompile(`(?m)^--` + regexp.QuoteMeta(candidate) + `(--)?\r?$`)
		if !pattern.MatchString(text) {
			return candidate
		}
		candidate = fmt.Sprintf("%s.%d", boundary, counter)
	}
}

// headerValue is compat32's fold of a str value with an unlimited line:
// ASCII text is written as is, anything else as one RFC 2047 word in UTF-8,
// base64 when shorter than quoted-printable.
func headerValue(value string) (string, error) {
	// Characters str.splitlines() splits on make Header.encode fold the
	// value (or the generator raise); they are outside the subset.
	if !utf8.ValidString(value) || utf8.RuneCountInString(value) > maxHeaderRunes || hasLineBreak(value) {
		return "", ErrUnsupported
	}
	if isASCII(value) {
		return value, nil
	}
	raw := []byte(value)
	length64 := (len(raw) + 2) / 3 * 4
	var qp strings.Builder
	for _, c := range raw {
		switch {
		case c == ' ':
			qp.WriteByte('_')
		case c >= 'a' && c <= 'z', c >= 'A' && c <= 'Z', c >= '0' && c <= '9', strings.IndexByte("-!*+/", c) >= 0:
			qp.WriteByte(c)
		default:
			fmt.Fprintf(&qp, "=%02X", c)
		}
	}
	if length64 < qp.Len() {
		return "=?utf-8?b?" + base64.StdEncoding.EncodeToString(raw) + "?=", nil
	}
	return "=?utf-8?q?" + qp.String() + "?=", nil
}

// bodyBase64 is base64mime.body_encode: 57-byte groups, one line each.
func bodyBase64(raw []byte) string {
	var out strings.Builder
	for start := 0; start < len(raw); start += 57 {
		end := min(start+57, len(raw))
		out.WriteString(base64.StdEncoding.EncodeToString(raw[start:end]))
		out.WriteByte('\n')
	}
	return out.String()
}

const addressSpecials = ".!#$%&'*+/=?^_`{|}~-"

// CheckEnvelope accepts addresses email.utils.parseaddr returns unchanged
// (so smtplib.quoteaddr only adds angle brackets): at most one '@' with a
// non-empty part on each side, made of ASCII letters, digits, the atext
// specials and dots, or non-ASCII letters (which smtplib then fails to
// encode, as Python does). A value without '@' (a provider login such as
// "apikey" used as email_smtp_user) is accepted on the same characters.
func CheckEnvelope(from string, to []string) error {
	for _, address := range append([]string{from}, to...) {
		if !plainAddress(address) {
			return ErrUnsupported
		}
	}
	return nil
}

func plainAddress(address string) bool {
	if !utf8.ValidString(address) || address == "" {
		return false
	}
	switch strings.Count(address, "@") {
	case 0:
	case 1:
		local, domain, _ := strings.Cut(address, "@")
		if local == "" || domain == "" {
			return false
		}
	default:
		return false
	}
	for _, r := range address {
		switch {
		case r == '@', r < 0x80 && (r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9'),
			r < 0x80 && strings.ContainsRune(addressSpecials, r), r >= 0x80 && unicode.IsLetter(r):
		default:
			return false
		}
	}
	return true
}
