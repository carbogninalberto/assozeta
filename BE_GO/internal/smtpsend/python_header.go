package smtpsend

import (
	"context"
	"fmt"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
)

// ErrHeader is the exception Python raises while a header is written: a
// continuation line that does not start with a space or a tab, which compat32
// refuses before anything is sent (email.errors.HeaderWriteError, and
// HeaderParseError for an embedded header). It matches ErrRaises because
// send_email's try turns it into a (False, e) answer too.
var ErrHeader = fmt.Errorf("%w: header value with a line break not followed by whitespace", ErrRaises)

// pySplitlines is str.splitlines().
func pySplitlines(s string) []string {
	var lines []string
	start := 0
	for i := 0; i < len(s); {
		r, size := utf8.DecodeRuneInString(s[i:])
		switch r {
		case '\n', '\r', '\v', '\f', 0x1c, 0x1d, 0x1e, 0x85, 0x2028, 0x2029:
			lines = append(lines, s[start:i])
			i += size
			if r == '\r' && i < len(s) && s[i] == '\n' {
				i++
			}
			start = i
			continue
		}
		i += size
	}
	if start < len(s) {
		lines = append(lines, s[start:])
	}
	return lines
}

// foldHeader is the value compat32 writes for "name: value" (the generator's
// policy.fold: Header(value, header_name=name).encode(maxlinelen=0), with
// charset=UNKNOWN8BIT when the text has lone surrogates). ErrHeader when
// Python raises HeaderWriteError, an error matching ErrRaises when the charset
// cannot encode the text.
func foldHeader(name, value string) (string, error) { return foldHeaderLine(name, value, 0) }

// foldHeaderLine is foldHeader with an explicit maximum line length, as
// Header.encode(maxlinelen) takes it (0 means no wrapping).
func foldHeaderLine(name, value string, maxlinelen int) (string, error) {
	runes := pythonRunes(value)
	// Header text is a Python str: environment bytes decoded with
	// surrogateescape are the WTF-8 form the rest of the package reads.
	text := pystr.String(runes)
	charset := charsetUSASCII
	switch {
	case hasSurrogate(runes):
		// email/_policybase.py _fold: Header(value, charset=UNKNOWN8BIT).
		charset = charsetUnknown8bit
	case !isASCII(text):
		// Header.append switches a value us-ascii cannot encode to UTF8.
		charset = charsetUTF8
	}
	return headerEncode([]headerChunk{{text: text, charset: charset}}, maxlinelen, name, " ")
}

// RenderAny is Render: header text is folded the way compat32 does, for any
// value a Python str can hold. ErrHeader reports a value Python raises on and
// an error matching ErrRaises a charset that cannot encode it.
func RenderAny(m Message, boundary string) (string, error) { return render(m, boundary) }

// Deliver is Send: send_email's delivery connection, where the envelope
// addresses go through smtplib.quoteaddr's parse of any text, exactly as
// Send's do.
func Deliver(ctx context.Context, s Server, from string, to []string, message string) error {
	return Send(ctx, s, from, to, message)
}

// VerifySendAny is VerifySend for a from text whose envelope address is the
// text itself: sendmail(given, to, message), both through smtplib.quoteaddr's
// parse of any text.
func VerifySendAny(ctx context.Context, s Server, given, to string, message []byte) error {
	c, err := open(ctx, s)
	if err != nil {
		return err
	}
	defer c.close()
	if err = c.login(s.User, s.Password); err != nil {
		return err
	}
	if err = c.sendmailFrom(given, given, []string{to}, message); err != nil {
		return err
	}
	return c.quit()
}
