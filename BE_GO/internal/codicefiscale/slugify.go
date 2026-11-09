package codicefiscale

import (
	"strings"
	"sync"
	"unicode/utf8"

	"golang.org/x/text/unicode/norm"
)

// unidecodeRange is a slice of the text_unidecode table: the replacements of
// code points lo..hi-1, joined by NUL like data.bin.
type unidecodeRange struct {
	lo, hi rune
	packed string
}

var (
	unidecodeOnce  sync.Once
	unidecodeSplit [len(unidecodeRanges)][]string
)

// combiningGraphemeJoiner is inserted by norm when a run of non-starters is
// longer than the Stream-Safe Text Format allows; Python never inserts it.
const combiningGraphemeJoiner = '͏'

// unidecode returns text_unidecode's replacement of r; ok is false when r
// falls outside the embedded subset of the table.
func unidecode(r rune) (repl string, ok bool) {
	switch {
	case r == 0:
		return "\x00", true
	case r > unidecodeTableLen:
		return "", true // IndexError in text_unidecode: dropped
	}
	unidecodeOnce.Do(func() {
		for i, rg := range unidecodeRanges {
			unidecodeSplit[i] = strings.Split(rg.packed, "\x00")
		}
	})
	for i, rg := range unidecodeRanges {
		if r >= rg.lo && r < rg.hi {
			return unidecodeSplit[i][r-rg.lo], true
		}
	}
	return "", false
}

// slugify ports python-slugify 8.0.4 slugify(text) with default options.
// It returns ErrUnsupported for input outside the ported subset.
func slugify(text string) (string, error) {
	if !utf8.ValidString(text) { // also rejects WTF-8 encoded lone surrogates
		return "", ErrUnsupported
	}
	text = replaceQuotes(text, "-")
	text = norm.NFKD.String(text)

	var b strings.Builder
	for _, r := range text {
		if r == combiningGraphemeJoiner {
			return "", ErrUnsupported
		}
		repl, ok := unidecode(r)
		if !ok {
			return "", ErrUnsupported
		}
		b.WriteString(repl)
	}
	text = b.String() // ASCII from here on: NFKD is the identity

	if hasHTMLReference(text) {
		return "", ErrUnsupported
	}
	text = strings.ToLower(text)
	text = replaceQuotes(text, "")
	text = removeDigitCommas(text)

	b.Reset()
	dash := true // trims leading dashes and collapses runs
	for i := 0; i < len(text); i++ {
		c := text[i]
		if isASCIIAlnum(c) {
			b.WriteByte(c)
			dash = false
		} else if !dash {
			b.WriteByte('-')
			dash = true
		}
	}
	return strings.TrimRight(b.String(), "-"), nil
}

// replaceQuotes is QUOTE_PATTERN.sub(repl, text): every run of ' becomes repl.
func replaceQuotes(text, repl string) string {
	if !strings.Contains(text, "'") {
		return text
	}
	var b strings.Builder
	inRun := false
	for _, r := range text {
		if r == '\'' {
			if !inRun {
				b.WriteString(repl)
			}
			inRun = true
			continue
		}
		inRun = false
		b.WriteRune(r)
	}
	return b.String()
}

// removeDigitCommas is NUMBERS_PATTERN.sub("", text) for ASCII text.
func removeDigitCommas(text string) string {
	var b strings.Builder
	for i := 0; i < len(text); i++ {
		if text[i] == ',' && i > 0 && i+1 < len(text) && isASCIIDigit(text[i-1]) && isASCIIDigit(text[i+1]) {
			continue
		}
		b.WriteByte(text[i])
	}
	return b.String()
}

// hasHTMLReference reports whether CHAR_ENTITY_PATTERN, DECIMAL_PATTERN or
// HEX_PATTERN would match the ASCII text.
func hasHTMLReference(text string) bool {
	for i := strings.IndexByte(text, '&'); i >= 0; {
		rest := text[i+1:]
		if end := strings.IndexByte(rest, ';'); end >= 0 {
			body := rest[:end]
			if _, ok := htmlEntityNames[body]; ok {
				return true
			}
			if dec, ok := strings.CutPrefix(body, "#"); ok && dec != "" && allBytes(dec, isASCIIDigit) {
				return true
			}
			if hex, ok := strings.CutPrefix(body, "#x"); ok && hex != "" && allBytes(hex, isASCIIHexDigit) {
				return true
			}
		}
		next := strings.IndexByte(rest, '&')
		if next < 0 {
			break
		}
		i += 1 + next
	}
	return false
}

func allBytes(s string, pred func(byte) bool) bool {
	for i := 0; i < len(s); i++ {
		if !pred(s[i]) {
			return false
		}
	}
	return true
}

func isASCIIDigit(c byte) bool { return '0' <= c && c <= '9' }

func isASCIIHexDigit(c byte) bool {
	return isASCIIDigit(c) || ('a' <= c && c <= 'f') || ('A' <= c && c <= 'F')
}

func isASCIIAlnum(c byte) bool {
	return isASCIIDigit(c) || ('a' <= c && c <= 'z') || ('A' <= c && c <= 'Z')
}
