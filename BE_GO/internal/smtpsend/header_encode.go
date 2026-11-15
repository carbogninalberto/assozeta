package smtpsend

// Header folding of CPython 3.13's email.header: Header.encode with the
// _ValueFormatter and _Accumulator it folds through, and the encoded words
// Charset.header_encode_lines splits. compat32 writes every header value with
// it (email/_policybase.py _fold, maxlinelen=0 for as_string()), and Django's
// EmailMessage folds the same class with maxlinelen=76, so headerEncode takes
// both the chunks and the line length.

import (
	"encoding/base64"
	"fmt"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// errNotPythonStr is text Go holds as bytes that are neither UTF-8 nor the
// WTF-8 form of a lone surrogate (pystr.Runes would have to invent U+FFFD).
// Request and stored text never is, but process environment values
// (WHITELABEL_NAME, EMAIL_HOST_USER) are read as raw bytes, where Python
// decodes them with surrogateescape and sends. Left to Django.
var errNotPythonStr = refusal.New("smtpsend: text not representable as a Python str")

// pythonRunes decodes value as the str it stands for: UTF-8, with the
// three-byte form of a lone surrogate (0xED 0xA0-0xBF 0x80-0xBF) becoming
// that surrogate. ok is false for any other invalid byte.
func pythonRunes(value string) ([]rune, bool) {
	runes := pystr.Runes(value)
	return runes, pystr.String(runes) == value
}

// hasSurrogate is email.header._has_surrogates: a code point in
// U+D800..U+DFFF.
func hasSurrogate(runes []rune) bool {
	for _, r := range runes {
		if r >= 0xD800 && r <= 0xDFFF {
			return true
		}
	}
	return false
}

// headerCharset is one of the charsets compat32 puts on a header chunk:
// Charset('us-ascii') (no header encoding), Charset('utf-8') and
// Charset('unknown-8bit') (both SHORTEST, their own name as output codec).
type headerCharset int

const (
	charsetUSASCII headerCharset = iota
	charsetUTF8
	charsetUnknown8bit
)

// name is the charset name in the RFC 2047 chrome, Charset.get_output_charset.
func (c headerCharset) name() string {
	switch c {
	case charsetUTF8:
		return "utf-8"
	case charsetUnknown8bit:
		return "unknown-8bit"
	}
	return "us-ascii"
}

// encoded is the charset's header encoding: none for us-ascii, SHORTEST
// (base64 or quoted-printable, whichever is shorter) for the other two.
func (c headerCharset) encoded() bool { return c != charsetUSASCII }

// headerChunk is one (string, charset) pair of Header._chunks.
type headerChunk struct {
	text    string
	charset headerCharset
}

// headerEncode is Header(chunks).encode(linesep="\n", maxlinelen) for chunks
// that carry their charset: the value of the header, without its name and
// without a trailing line separator. maxlinelen 0 means no wrapping, as
// Header.encode turns it into 1,000,000. headerName is the field name the
// first line accounts for ("" for none) and continuationWS the continuation
// whitespace (compat32 uses " ").
//
// ErrHeader reports the HeaderWriteError the generator raises for a folded
// line that does not start with whitespace; a UnicodeEncodeError while
// encoding a chunk is returned wrapped in ErrRaises.
func headerEncode(chunks []headerChunk, maxlinelen int, headerName, continuationWS string) (string, error) {
	if maxlinelen == 0 {
		maxlinelen = 1000000
	}
	headerlen := 0
	if headerName != "" {
		headerlen = len(headerName) + 2
	}
	formatter := &headerFormatter{maxlen: maxlinelen, continuationWS: continuationWS, splitchars: ";, \t"}
	formatter.current.initial = headerlen
	normalized := normalizeHeaderChunks(chunks)
	lastcs := charsetUSASCII
	lastspace, hasspace, started := false, false, false
	for _, chunk := range normalized {
		if started {
			// Only the second and later chunks need a separating space: from a
			// charset to us-ascii when the text does not start with whitespace,
			// and from us-ascii to a charset when the previous text did not end
			// with one (email/header.py Header.encode).
			hasspace = chunk.text != "" && nonCText(firstRune(chunk.text))
			if lastcs.encoded() {
				if !hasspace || !chunk.charset.encoded() {
					formatter.addTransition()
				}
			} else if chunk.charset.encoded() && !lastspace {
				formatter.addTransition()
			}
		}
		lastspace = chunk.text != "" && nonCText(lastRune(chunk.text))
		lastcs, started, hasspace = chunk.charset, true, false
		lines := pySplitlines(chunk.text)
		first := ""
		if len(lines) > 0 {
			first = lines[0]
		}
		if err := formatter.feed("", first, chunk.charset); err != nil {
			return "", err
		}
		for _, line := range lines[min(1, len(lines)):] {
			formatter.newline()
			if chunk.charset.encoded() {
				if err := formatter.feed(continuationWS, " "+pyLStrip(line), chunk.charset); err != nil {
					return "", err
				}
				continue
			}
			stripped := pyLStrip(line)
			if err := formatter.feed(line[:len(line)-len(stripped)], stripped, chunk.charset); err != nil {
				return "", err
			}
		}
		if len(lines) > 1 {
			formatter.newline()
		}
	}
	if len(normalized) > 0 {
		formatter.addTransition()
	}
	value := formatter.str()
	// verify_generated_headers (email/generator.py NEWLINE_WITHOUT_FWSP): a
	// folded line that does not start with folding whitespace would read as a
	// new header.
	for _, line := range strings.Split(value, "\n")[1:] {
		if line == "" || line[0] != ' ' && line[0] != '\t' {
			return "", ErrHeader
		}
	}
	return value, nil
}

// normalizeHeaderChunks is Header._normalize: runs of chunks with the same
// charset are joined with a space.
func normalizeHeaderChunks(chunks []headerChunk) []headerChunk {
	var out []headerChunk
	var joined []string
	for i, chunk := range chunks {
		if i > 0 && chunk.charset != chunks[i-1].charset {
			out = append(out, headerChunk{strings.Join(joined, " "), chunks[i-1].charset})
			joined = joined[:0]
		}
		joined = append(joined, chunk.text)
	}
	if len(chunks) > 0 {
		out = append(out, headerChunk{strings.Join(joined, " "), chunks[len(chunks)-1].charset})
	}
	return out
}

func firstRune(s string) rune {
	r, _ := utf8.DecodeRuneInString(s)
	return r
}

func lastRune(s string) rune {
	r, _ := utf8.DecodeLastRuneInString(s)
	return r
}

// nonCText is Header._nonctext: whitespace, or one of '(' ')' '\'.
func nonCText(r rune) bool { return pySpace(r) || r == '(' || r == ')' || r == '\\' }

// pyLStrip is str.lstrip(): the leading characters str.isspace() accepts.
func pyLStrip(s string) string {
	for i, r := range s {
		if !pySpace(r) {
			return s[i:]
		}
	}
	return ""
}

// pyOnlySpaceText is str.isspace() of the whole text.
func pyOnlySpaceText(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if !pySpace(r) {
			return false
		}
	}
	return true
}

// headerAccumulator is email.header._Accumulator: the (fws, part) pairs of the line
// being built, plus the header name length it starts with. length caches
// len(self): every part pushed here is ASCII (a us-ascii chunk holds ASCII
// text, and an encoded word is ASCII), so Python character counts and Go byte
// counts agree.
type headerAccumulator struct {
	parts   []foldChunk
	initial int
	length  int
}

func (l *headerAccumulator) push(fws, part string) {
	l.parts = append(l.parts, foldChunk{fws: fws, part: part})
	l.length += len(fws) + len(part)
}

func (l *headerAccumulator) size() int { return l.initial + l.length }

func (l *headerAccumulator) text() string {
	var out strings.Builder
	for _, part := range l.parts {
		out.WriteString(part.fws)
		out.WriteString(part.part)
	}
	return out.String()
}

// pop removes the last pair; an empty line pops the (”, ”) _Accumulator
// returns.
func (l *headerAccumulator) pop() foldChunk {
	n := len(l.parts)
	if n == 0 {
		return foldChunk{}
	}
	last := l.parts[n-1]
	l.parts = l.parts[:n-1]
	l.length -= len(last.fws) + len(last.part)
	return last
}

// popFrom removes and returns the pairs from i on.
func (l *headerAccumulator) popFrom(i int) []foldChunk {
	popped := append([]foldChunk(nil), l.parts[i:]...)
	for _, part := range popped {
		l.length -= len(part.fws) + len(part.part)
	}
	l.parts = l.parts[:i]
	return popped
}

// reset starts a line over with the given pairs and no header name.
func (l *headerAccumulator) reset(parts []foldChunk) {
	l.parts, l.initial, l.length = parts, 0, 0
	for _, part := range parts {
		l.length += len(part.fws) + len(part.part)
	}
}

// onlyWhitespace is _Accumulator.is_onlyws.
func (l *headerAccumulator) onlyWhitespace() bool {
	return l.initial == 0 && (len(l.parts) == 0 || pyOnlySpaceText(l.text()))
}

// headerFormatter is email.header._ValueFormatter.
type headerFormatter struct {
	maxlen         int
	continuationWS string
	splitchars     string
	lines          []string
	current        headerAccumulator
}

// newline is _ValueFormatter.newline: the line's last pair moves to the next
// line unless it is the transition pair, and a whitespace-only line is
// appended to the previous one.
func (f *headerFormatter) newline() {
	end := f.current.pop()
	if end != (foldChunk{fws: " "}) {
		f.current.push(end.fws, end.part)
	}
	if f.current.size() > 0 {
		if f.current.onlyWhitespace() && len(f.lines) > 0 {
			f.lines[len(f.lines)-1] += f.current.text()
		} else {
			f.lines = append(f.lines, f.current.text())
		}
	}
	f.current.reset(nil)
}

// addTransition is _ValueFormatter.add_transition.
func (f *headerFormatter) addTransition() { f.current.push(" ", "") }

// str is _ValueFormatter._str("\n").
func (f *headerFormatter) str() string {
	f.newline()
	return strings.Join(f.lines, "\n")
}

// nextMaxlen is _ValueFormatter._maxlengths: the first line also carries the
// header name, every later one starts with the continuation whitespace.
func (f *headerFormatter) nextMaxlen() func() int {
	first := true
	return func() int {
		if first {
			first = false
			return f.maxlen - f.current.size()
		}
		return f.maxlen - len(f.continuationWS)
	}
}

// feed is _ValueFormatter.feed: us-ascii text splits at the syntactic breaks,
// anything else becomes RFC 2047 encoded words.
func (f *headerFormatter) feed(fws, text string, charset headerCharset) error {
	if !charset.encoded() {
		f.asciiSplit(fws, text)
		return nil
	}
	lines, err := headerEncodeLines(text, charset, f.nextMaxlen())
	if err != nil {
		return err
	}
	if len(lines) == 0 {
		return nil
	}
	first := lines[0]
	lines = lines[1:]
	if !first.none {
		f.appendChunk(fws, first.word)
	}
	if len(lines) == 0 {
		return nil
	}
	last := lines[len(lines)-1]
	lines = lines[:len(lines)-1]
	f.newline()
	f.current.push(f.continuationWS, last.word)
	for _, line := range lines {
		f.lines = append(f.lines, f.continuationWS+line.word)
	}
	return nil
}

// asciiSplit is _ValueFormatter._ascii_split: re.split("([ \t]+)", fws+text)
// as (fws, part) pairs.
func (f *headerFormatter) asciiSplit(fws, text string) {
	whole := fws + text
	last, pendingFWS, first := 0, "", true
	for _, loc := range foldWhitespace.FindAllStringIndex(whole, -1) {
		if part := whole[last:loc[0]]; !first || part != "" {
			f.appendChunk(pendingFWS, part)
		}
		first, pendingFWS, last = false, whole[loc[0]:loc[1]], loc[1]
	}
	if part := whole[last:]; !first || part != "" {
		f.appendChunk(pendingFWS, part)
	}
}

// appendChunk is _ValueFormatter._append_chunk: a line over the limit splits
// after the last preferred character, or leaves the header name on its own
// line when there is no such character.
func (f *headerFormatter) appendChunk(fws, part string) {
	f.current.push(fws, part)
	if f.current.size() <= f.maxlen {
		return
	}
	split := -1
	for _, ch := range []byte(f.splitchars) {
		for i := len(f.current.parts) - 1; i > 0; i-- {
			if ch == ' ' || ch == '\t' {
				if pair := f.current.parts[i]; pair.fws != "" && pair.fws[0] == ch {
					split = i
					break
				}
			}
			if prev := f.current.parts[i-1].part; prev != "" && prev[len(prev)-1] == ch {
				split = i
				break
			}
		}
		if split >= 0 {
			break
		}
	}
	if split < 0 {
		last := f.current.pop()
		if f.current.initial > 0 {
			// There is a header before it, so it goes on a line of its own.
			f.newline()
			if last.fws == "" {
				last.fws = " "
			}
		}
		f.current.push(last.fws, last.part)
		return
	}
	remainder := f.current.popFrom(split)
	f.lines = append(f.lines, f.current.text())
	f.current.reset(remainder)
}

// headerEncoder is the module Charset._get_encoder picks: email.base64mime or
// email.quoprimime.
type headerEncoder int

const (
	encodeQuotedPrintable headerEncoder = iota
	encodeBase64
)

// encodedLine is one element of Charset.header_encode_lines: an encoded word,
// or the None that marks a line where not even one character fit.
type encodedLine struct {
	word string
	none bool
}

// headerEncodeLines is Charset.header_encode_lines: the encoded words for
// text, each fitting the line lengths nextMaxlen yields (without the RFC 2047
// chrome the charset name and "=??" add).
func headerEncodeLines(text string, charset headerCharset, nextMaxlen func() int) ([]encodedLine, error) {
	raw, err := headerTextBytes(text, charset)
	if err != nil {
		return nil, err
	}
	encoder := headerEncoderFor(charset, raw)
	extra := len(charset.name()) + 7
	runes := pystr.Runes(text)
	var lines []encodedLine
	lineStart, offset, maxlen := 0, 0, nextMaxlen()-extra
	curBytes, qpLength := 0, 0
	for _, r := range runes {
		n := charset.runeBytes(r)
		cost := qpHeaderLength(raw[offset : offset+n])
		curBytes, qpLength, offset = curBytes+n, qpLength+cost, offset+n
		if encoder.length(curBytes, qpLength) <= maxlen {
			continue
		}
		// The character does not fit: the line before it is written and the
		// character starts the next one.
		offset -= n
		if len(lines) == 0 && curBytes == n {
			lines = append(lines, encodedLine{none: true})
		} else {
			lines = append(lines, encodedLine{word: headerWord(raw[lineStart:offset], encoder, charset)})
		}
		lineStart, curBytes, qpLength = offset, n, cost
		offset += n
		maxlen = nextMaxlen() - extra
	}
	lines = append(lines, encodedLine{word: headerWord(raw[lineStart:], encoder, charset)})
	return lines, nil
}

// headerTextBytes is Charset._encode(text, output codec): UTF-8 for the utf-8
// charset, 'ascii' with 'surrogateescape' for unknown-8bit (U+DC80..U+DCFF
// become 0x80..0xFF; anything else that is not ASCII raises), and 'us-ascii'.
func headerTextBytes(text string, charset headerCharset) ([]byte, error) {
	raw := make([]byte, 0, len(text))
	for _, r := range pystr.Runes(text) {
		switch charset {
		case charsetUnknown8bit:
			switch {
			case r < 0x80:
				raw = append(raw, byte(r))
			case r >= 0xDC80 && r <= 0xDCFF:
				raw = append(raw, byte(r-0xDC00))
			default:
				return nil, fmt.Errorf("%w: unknown-8bit header text with character %U", ErrRaises, r)
			}
		case charsetUTF8:
			if r >= 0xD800 && r <= 0xDFFF {
				return nil, fmt.Errorf("%w: utf-8 header text with surrogate %U", ErrRaises, r)
			}
			raw = utf8.AppendRune(raw, r)
		default:
			if r >= 0x80 {
				return nil, fmt.Errorf("%w: us-ascii header text with character %U", ErrRaises, r)
			}
			raw = append(raw, byte(r))
		}
	}
	return raw, nil
}

// runeBytes is the number of bytes _encode gives one character.
func (c headerCharset) runeBytes(r rune) int {
	if c == charsetUTF8 {
		return utf8.RuneLen(r)
	}
	return 1
}

// headerEncoderFor is Charset._get_encoder: SHORTEST takes base64 only when it
// is shorter than quoted-printable for the whole text.
func headerEncoderFor(charset headerCharset, raw []byte) headerEncoder {
	if (len(raw)+2)/3*4 < qpHeaderLength(raw) {
		return encodeBase64
	}
	return encodeQuotedPrintable
}

// length is the module's header_length: the encoded length without chrome.
func (e headerEncoder) length(bytes, qp int) int {
	if e == encodeBase64 {
		return (bytes + 2) / 3 * 4
	}
	return qp
}

// qpHeaderLength is email.quoprimime.header_length.
func qpHeaderLength(raw []byte) int {
	total := 0
	for _, b := range raw {
		total += qpHeaderCost(b)
	}
	return total
}

// qpHeaderCost is the length of one octet in an RFC 2047 'q' word: itself for
// the safe characters and the space (written as '_'), '=XX' for the rest.
func qpHeaderCost(b byte) int {
	switch {
	case b >= 'a' && b <= 'z', b >= 'A' && b <= 'Z', b >= '0' && b <= '9', b == ' ', strings.IndexByte("-!*+/", b) >= 0:
		return 1
	}
	return 3
}

// headerWord is email.base64mime.header_encode or
// email.quoprimime.header_encode: the word, or "" for no bytes.
func headerWord(raw []byte, encoder headerEncoder, charset headerCharset) string {
	if len(raw) == 0 {
		return ""
	}
	name := charset.name()
	if encoder == encodeBase64 {
		return "=?" + name + "?b?" + base64.StdEncoding.EncodeToString(raw) + "?="
	}
	var word strings.Builder
	word.WriteString("=?" + name + "?q?")
	for _, b := range raw {
		if qpHeaderCost(b) == 1 {
			if b == ' ' {
				b = '_'
			}
			word.WriteByte(b)
			continue
		}
		fmt.Fprintf(&word, "=%02X", b)
	}
	word.WriteString("?=")
	return word.String()
}
