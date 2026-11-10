package smtpsend

import (
	"context"
	"errors"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/handoff"
)

// ErrHeader is the exception Python raises while a header is written: a
// continuation line that does not start with a space or a tab
// (email.errors.HeaderParseError, HeaderWriteError).
var ErrHeader = errors.New("smtpsend: header value with a line break not followed by whitespace")

// maxHeaderBytes keeps every encoded header line below the 1,000,000
// characters as_string(maxheaderlen=0) gives Header.encode, so no line is
// split.
const maxHeaderBytes = 200000

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

func pyOnlySpace(s string) bool {
	return s != "" && strings.TrimFunc(s, pySpace) == ""
}

// foldHeader is the value compat32 writes for "name: value" with an
// unlimited line (Header(value, header_name=name).encode(maxlinelen=0) and
// the generator's check of the folded text): us-ascii text line by line,
// anything else as one RFC 2047 word per line. ErrHeader when Python raises.
func foldHeader(name, value string) (string, error) {
	if !utf8.ValidString(value) || len(value) > maxHeaderBytes {
		return "", ErrUnsupported
	}
	ascii := isASCII(value)
	var lines []string
	current := &foldLine{initial: len(name) + 2}
	newline := func() {
		end := current.pop()
		if end != (foldChunk{fws: " "}) {
			current.parts = append(current.parts, end)
		}
		if current.size() > 0 {
			if text := current.String(); current.initial == 0 && pyOnlySpace(text) && len(lines) > 0 {
				lines[len(lines)-1] += text
			} else {
				lines = append(lines, text)
			}
		}
		current = &foldLine{}
	}
	feed := func(fws, text string) {
		if !ascii {
			encoded := ""
			if text != "" {
				encoded = HeaderEncode(text)
			}
			current.parts = append(current.parts, foldChunk{fws, encoded})
			return
		}
		// _ascii_split: re.split("([ \t]+)", fws+text) as (fws, part) pairs.
		whole := fws + text
		last, pendingFWS, first := 0, "", true
		for _, loc := range foldWhitespace.FindAllStringIndex(whole, -1) {
			if part := whole[last:loc[0]]; !first || part != "" {
				current.parts = append(current.parts, foldChunk{pendingFWS, part})
			}
			first, pendingFWS, last = false, whole[loc[0]:loc[1]], loc[1]
		}
		if part := whole[last:]; !first || part != "" {
			current.parts = append(current.parts, foldChunk{pendingFWS, part})
		}
	}
	source := pySplitlines(value)
	if len(source) > 0 {
		feed("", source[0])
	} else {
		feed("", "")
	}
	for _, line := range source[min(1, len(source)):] {
		newline()
		stripped := strings.TrimLeftFunc(line, pySpace)
		if ascii {
			feed(line[:len(line)-len(stripped)], stripped)
		} else {
			feed(" ", " "+stripped)
		}
	}
	if len(source) > 1 {
		newline()
	}
	current.parts = append(current.parts, foldChunk{fws: " "}) // add_transition
	newline()
	for _, line := range lines[min(1, len(lines)):] {
		if line == "" || line[0] != ' ' && line[0] != '\t' {
			return "", ErrHeader
		}
	}
	return strings.Join(lines, "\n"), nil
}

// RenderAny is Render for any header text: line breaks are folded the way
// Python does and ErrHeader reports a message Python raises on. ErrUnsupported
// is left for text that is not valid UTF-8 or a header over maxHeaderBytes.
func RenderAny(m Message, boundary string) (string, error) {
	return render(m, boundary, foldHeader)
}

// Deliver is Send for any UTF-8 envelope: the addresses go through
// smtplib.quoteaddr's parse, and a command that is not ASCII fails the way
// Python's encoding of it does (after the commands before it were sent).
func Deliver(ctx context.Context, s Server, from string, to []string, message string) error {
	for _, address := range append([]string{from}, to...) {
		if !utf8.ValidString(address) {
			return ErrUnsupported
		}
	}
	if !isASCII(message) {
		return ErrUnsupported
	}
	handoff.MarkEffect(ctx, "smtp send")
	c, err := open(ctx, s)
	if err != nil {
		return err
	}
	defer c.close()
	c.anyAddress = true
	if err = c.login(s.User, s.Password); err != nil {
		return err
	}
	if err = c.sendmail(from, to, message); err != nil {
		return err
	}
	return c.quit()
}

// VerifySendAny is VerifySend for any UTF-8 from text and recipient:
// sendmail(given, to, message) with smtplib.quoteaddr's parse of both.
func VerifySendAny(ctx context.Context, s Server, given, to string, message []byte) error {
	if !utf8.ValidString(given) || !utf8.ValidString(to) {
		return ErrUnsupported
	}
	c, err := open(ctx, s)
	if err != nil {
		return err
	}
	defer c.close()
	c.anyAddress = true
	if err = c.login(s.User, s.Password); err != nil {
		return err
	}
	if err = c.sendmailFrom(given, given, []string{to}, message); err != nil {
		return err
	}
	return c.quit()
}
