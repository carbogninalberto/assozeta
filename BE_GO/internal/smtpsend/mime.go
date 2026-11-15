package smtpsend

import (
	"encoding/base64"
	"fmt"
	"math"
	"math/rand/v2"
	"regexp"
	"strings"
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

// Render returns msg.as_string() for the MIMEMultipart send_email builds,
// with boundary as the generated boundary (Python's random base; the
// collision suffix is applied here as _make_boundary does).
func Render(m Message, boundary string) (string, error) { return render(m, boundary) }

// render writes the headers compat32's fold gives for any value (foldHeader:
// the same folding as RenderAny's) and the base64 text part MIMEText builds.
func render(m Message, boundary string) (string, error) {
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
	// msg.attach(MIMEText(body, content, 'utf-8')) runs before as_string():
	// Message.set_payload encodes the text with
	// body.encode('utf-8', 'surrogateescape'), so a lone surrogate
	// U+DC80..U+DCFF becomes the byte it escapes and any other one raises
	// UnicodeEncodeError ("surrogates not allowed", models.py:154-156).
	bodyRunes, ok := pythonRunes(body)
	if !ok {
		return "", errNotPythonStr
	}
	raw, err := bodyTextBytes(bodyRunes)
	if err != nil {
		return "", err
	}
	var folded []string
	for _, header := range headers {
		value, err := foldHeader(header[0], header[1])
		if err != nil {
			return "", err
		}
		folded = append(folded, header[0]+": "+value+"\n")
	}
	part := "Content-Type: text/" + content + "; charset=\"utf-8\"\nMIME-Version: 1.0\n" +
		"Content-Transfer-Encoding: base64\n\n" + bodyBase64(raw)
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

// bodyTextBytes is Message.set_payload's text.encode('utf-8',
// 'surrogateescape') for the utf-8 MIMEText send_email attaches: the UTF-8
// bytes of the text, with a lone surrogate U+DC80..U+DCFF carrying the byte it
// escapes. Any other surrogate raises UnicodeEncodeError ('surrogates not
// allowed').
func bodyTextBytes(runes []rune) ([]byte, error) {
	raw := make([]byte, 0, len(runes))
	for _, r := range runes {
		switch {
		case r >= 0xDC80 && r <= 0xDCFF:
			raw = append(raw, byte(r-0xDC00))
		case r >= 0xD800 && r <= 0xDFFF:
			return nil, fmt.Errorf("%w: body text with surrogate %U", ErrRaises, r)
		default:
			raw = utf8.AppendRune(raw, r)
		}
	}
	return raw, nil
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
