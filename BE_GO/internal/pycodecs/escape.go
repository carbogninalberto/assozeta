package pycodecs

// This file ports CPython 3.13's unicode_escape and raw_unicode_escape
// decoders (_PyUnicode_DecodeUnicodeEscapeInternal2 and
// _PyUnicode_DecodeRawUnicodeEscapeStateful in Objects/unicodeobject.c),
// plus the Unicode character-name lookup used by "\N{...}".
//
// CPython reports both codecs under names without the hyphen
// ("unicodeescape", "rawunicodeescape"), and positions are byte offsets into
// the input. Escape decoding has a few easy-to-miss details:
//   - an invalid escape such as "\q" is kept verbatim (a DeprecationWarning,
//     not an error);
//   - an octal escape may exceed 0xFF ("\777" is U+01FF); it is only a
//     warning;
//   - a failed hex escape consumes the backslash, the prefix and the valid
//     digits, but not the byte that was not a hex digit; "truncated \xXX
//     escape" is used for both truncation and a bad digit;
//   - "\N{...}" is looked up with unicodedata.lookup-like rules but named
//     sequences are rejected and aliases are accepted; a name is matched
//     ASCII-case-insensitively. Hangul syllable names and the derived
//     "CJK UNIFIED IDEOGRAPH-XXXX" / "TANGUT IDEOGRAPH-XXXX" names are
//     computed instead of stored;
//   - in raw_unicode_escape a trailing backslash is literal and only \u and
//     \U are escapes; an out-of-range \U says "\Uxxxxxxxx out of range".

import (
	"bufio"
	"bytes"
	"compress/gzip"
	_ "embed"
	"strconv"
	"strings"
)

func init() {
	register("unicode-escape", decodeUnicodeEscape)
	register("raw-unicode-escape", decodeRawUnicodeEscape)
	unicodeEscapeNames = loadUnicodeEscapeNames(unicodeEscapeNameData)
}

// ---------------------------------------------------------------------------
// Character names

// unicodeEscapeNameData is the gzip-compressed "HEX NAME\n" table generated
// from the reference Python's unicodedata by testdata/gen_unicode.py. It
// holds every non-derived canonical character name plus every formal name
// alias; the derived names are computed below.
//
//go:embed unicode_names.bin.gz
var unicodeEscapeNameData []byte

// unicodeEscapeNames maps an upper-case name to its code point.
var unicodeEscapeNames map[string]rune

// loadUnicodeEscapeNames reads the embedded table.
func loadUnicodeEscapeNames(blob []byte) map[string]rune {
	zr, err := gzip.NewReader(bytes.NewReader(blob))
	if err != nil {
		panic("pycodecs: unicode_names.bin.gz: " + err.Error())
	}
	defer zr.Close()
	names := make(map[string]rune, 40000)
	sc := bufio.NewScanner(zr)
	sc.Buffer(make([]byte, 256), 1<<20)
	for sc.Scan() {
		line := sc.Text()
		sp := strings.IndexByte(line, ' ')
		if sp <= 0 {
			panic("pycodecs: unicode_names.bin.gz: malformed line")
		}
		cp, err := strconv.ParseUint(line[:sp], 16, 32)
		if err != nil {
			panic("pycodecs: unicode_names.bin.gz: " + err.Error())
		}
		names[line[sp+1:]] = rune(cp)
	}
	if err := sc.Err(); err != nil {
		panic("pycodecs: unicode_names.bin.gz: " + err.Error())
	}
	return names
}

// hangulJamo is CPython's hangul_syllables table: the short jamo names for
// the leading consonant (L), vowel (V) and trailing consonant (T) positions.
var hangulJamo = [28][3]string{
	{"G", "A", ""},
	{"GG", "AE", "G"},
	{"N", "YA", "GG"},
	{"D", "YAE", "GS"},
	{"DD", "EO", "N"},
	{"R", "E", "NJ"},
	{"M", "YEO", "NH"},
	{"B", "YE", "D"},
	{"BB", "O", "L"},
	{"S", "WA", "LG"},
	{"SS", "WAE", "LM"},
	{"", "OE", "LB"},
	{"J", "YO", "LS"},
	{"JJ", "U", "LT"},
	{"C", "WEO", "LP"},
	{"K", "WE", "LH"},
	{"T", "WI", "M"},
	{"P", "YU", "B"},
	{"H", "EU", "BS"},
	{"", "YI", "S"},
	{"", "I", "SS"},
	{"", "", "NG"},
	{"", "", "J"},
	{"", "", "C"},
	{"", "", "K"},
	{"", "", "T"},
	{"", "", "P"},
	{"", "", "H"},
}

// hangulFindSyllable is CPython's find_syllable: the longest jamo name in
// the first count rows of the column that prefixes s. pos is -1 when nothing
// matched. CPython passes LCount=19, VCount=21 and TCount=28 (the rows have
// no L or V short name outside those counts).
func hangulFindSyllable(s []byte, column, count int) (pos, length int) {
	pos, length = -1, -1
	for i := 0; i < count; i++ {
		entry := hangulJamo[i][column]
		if len(entry) <= length {
			continue
		}
		// A name cannot extend past the closing brace, so a longer entry
		// than the remaining bytes is no match.
		if len(entry) <= len(s) && string(s[:len(entry)]) == entry {
			pos, length = i, len(entry)
		}
	}
	if length < 0 {
		length = 0
	}
	return pos, length
}

// unicodeDerivedRange is one of CPython's derived_name_ranges.
type unicodeDerivedRange struct {
	first, last rune
	prefix      int // 0 Hangul, 1 CJK unified ideograph, 2 Tangut ideograph
}

var unicodeDerivedRanges = []unicodeDerivedRange{
	{0x3400, 0x4DBF, 1},
	{0x4E00, 0x9FFF, 1},
	{0xAC00, 0xD7A3, 0},
	{0x17000, 0x187F7, 2},
	{0x18D00, 0x18D08, 2},
	{0x20000, 0x2A6DF, 1},
	{0x2A700, 0x2B739, 1},
	{0x2B740, 0x2B81D, 1},
	{0x2B820, 0x2CEA1, 1},
	{0x2CEB0, 0x2EBE0, 1},
	{0x2EBF0, 0x2EE5D, 1},
	{0x30000, 0x3134A, 1},
	{0x31350, 0x323AF, 1},
}

// unicodeDerivedPrefixID is find_prefix_id: the prefix id of the first
// range containing cp, or -1.
func unicodeDerivedPrefixID(cp rune) int {
	for _, r := range unicodeDerivedRanges {
		if cp < r.first {
			return -1
		}
		if cp <= r.last {
			return r.prefix
		}
	}
	return -1
}

// parseUnicodeDerivedHex is parse_hex_code: 4 to 6 upper-case hex digits,
// no leading zero, at most U+10FFFF.
func parseUnicodeDerivedHex(s []byte) (rune, bool) {
	if len(s) < 4 || len(s) > 6 || s[0] == '0' {
		return 0, false
	}
	var v rune
	for _, c := range s {
		var d rune
		switch {
		case c >= '0' && c <= '9':
			d = rune(c - '0')
		case c >= 'A' && c <= 'F':
			d = rune(c-'A') + 10
		default:
			return 0, false
		}
		v = v*16 + d
	}
	if v > 0x10FFFF {
		return 0, false
	}
	return v, true
}

// derivedUnicodeName implements the prefix branches of CPython's _getcode.
// name has already been ASCII-upper-cased.
func derivedUnicodeName(name []byte) (rune, bool) {
	const hangulPrefix = "HANGUL SYLLABLE "
	if bytes.HasPrefix(name, []byte(hangulPrefix)) {
		rest := name[len(hangulPrefix):]
		l, n := hangulFindSyllable(rest, 0, 19)
		rest = rest[n:]
		v, n := hangulFindSyllable(rest, 1, 21)
		rest = rest[n:]
		t, n := hangulFindSyllable(rest, 2, 28)
		rest = rest[n:]
		if l < 0 || v < 0 || t < 0 || len(rest) != 0 {
			return 0, false
		}
		return 0xAC00 + rune((l*21+v)*28+t), true
	}

	const cjkPrefix = "CJK UNIFIED IDEOGRAPH-"
	if bytes.HasPrefix(name, []byte(cjkPrefix)) {
		cp, ok := parseUnicodeDerivedHex(name[len(cjkPrefix):])
		if !ok || unicodeDerivedPrefixID(cp) != 1 {
			return 0, false
		}
		return cp, true
	}

	const tangutPrefix = "TANGUT IDEOGRAPH-"
	if bytes.HasPrefix(name, []byte(tangutPrefix)) {
		cp, ok := parseUnicodeDerivedHex(name[len(tangutPrefix):])
		if !ok || unicodeDerivedPrefixID(cp) != 2 {
			return 0, false
		}
		return cp, true
	}
	return 0, false
}

// lookupUnicodeCharacterName is the "\N{...}" lookup: ASCII case-insensitive
// canonical names, formal aliases, Hangul syllables and derived CJK/Tangut
// names; named sequences are not accepted.
func lookupUnicodeCharacterName(name []byte) (rune, bool) {
	upper := make([]byte, len(name))
	for i, c := range name {
		if c >= 'a' && c <= 'z' {
			c -= 'a' - 'A'
		}
		upper[i] = c
	}
	if cp, ok := derivedUnicodeName(upper); ok {
		return cp, true
	}
	cp, ok := unicodeEscapeNames[string(upper)]
	return cp, ok
}

// ---------------------------------------------------------------------------
// unicode_escape

// escapeHexValue returns the value of one hex digit.
func escapeHexValue(c byte) (rune, bool) {
	switch {
	case c >= '0' && c <= '9':
		return rune(c - '0'), true
	case c >= 'a' && c <= 'f':
		return rune(c-'a') + 10, true
	case c >= 'A' && c <= 'F':
		return rune(c-'A') + 10, true
	}
	return 0, false
}

// escapeReadHex reads count hex digits from data at i. ok is false when the
// input ends or a byte is not a hex digit; the returned position is then the
// error end (CPython's endinpos), which points at the bad byte or at the end
// of the input.
func escapeReadHex(data []byte, i, count int) (value uint32, next int, ok bool) {
	for n := 0; n < count; n++ {
		if i >= len(data) {
			return 0, i, false
		}
		v, isHex := escapeHexValue(data[i])
		if !isHex {
			return 0, i, false
		}
		value = value<<4 | uint32(v)
		i++
	}
	return value, i, true
}

// decodeUnicodeEscape is _PyUnicode_DecodeUnicodeEscapeInternal with a final
// (non-incremental) decode.
func decodeUnicodeEscape(data []byte, sink *Sink) error {
	sink.SetCodec("unicodeescape")

	i := 0
	for i < len(data) {
		c := data[i]
		i++
		if c != '\\' {
			// Non-escape bytes are Unicode ordinals (a Latin-1-like copy).
			sink.Out = append(sink.Out, rune(c))
			continue
		}

		start := i - 1 // the backslash
		if i >= len(data) {
			if _, err := sink.Fail(start, len(data), "\\ at end of string"); err != nil {
				return err
			}
			i = len(data)
			continue
		}
		c = data[i]
		i++

		switch c {
		case '\n':
			// Backslash-newline is a line continuation and vanishes.
		case '\\':
			sink.Out = append(sink.Out, '\\')
		case '\'':
			sink.Out = append(sink.Out, '\'')
		case '"':
			sink.Out = append(sink.Out, '"')
		case 'b':
			sink.Out = append(sink.Out, '\b')
		case 'f':
			sink.Out = append(sink.Out, '\f')
		case 't':
			sink.Out = append(sink.Out, '\t')
		case 'n':
			sink.Out = append(sink.Out, '\n')
		case 'r':
			sink.Out = append(sink.Out, '\r')
		case 'v':
			sink.Out = append(sink.Out, '\v')
		case 'a':
			sink.Out = append(sink.Out, '\a')
		case '0', '1', '2', '3', '4', '5', '6', '7':
			// \OOO: one to three octal digits. Values above 0xFF are kept
			// (only a DeprecationWarning in 3.13).
			ch := rune(c - '0')
			for n := 0; n < 2 && i < len(data) && data[i] >= '0' && data[i] <= '7'; n++ {
				ch = ch<<3 | rune(data[i]-'0')
				i++
			}
			sink.Out = append(sink.Out, ch)
		case 'x', 'u', 'U':
			count := 2
			reason := "truncated \\xXX escape"
			if c == 'u' {
				count = 4
				reason = "truncated \\uXXXX escape"
			} else if c == 'U' {
				count = 8
				reason = "truncated \\UXXXXXXXX escape"
			}
			value, next, ok := escapeReadHex(data, i, count)
			if !ok {
				// Both a truncated escape and a non-hex digit use the
				// "truncated ..." message; the bad byte is not consumed.
				if _, err := sink.Fail(start, next, reason); err != nil {
					return err
				}
				i = next
				continue
			}
			if value > 0x10FFFF {
				if _, err := sink.Fail(start, next, "illegal Unicode character"); err != nil {
					return err
				}
				i = next
				continue
			}
			sink.Out = append(sink.Out, rune(value))
			i = next
		case 'N':
			// "\N" must be followed by "{name}".
			if i >= len(data) {
				if _, err := sink.Fail(start, len(data), "malformed \\N character escape"); err != nil {
					return err
				}
				i = len(data)
				continue
			}
			if data[i] != '{' {
				// The malformed span is only the "\N"; the next byte is
				// decoded normally.
				if _, err := sink.Fail(start, i, "malformed \\N character escape"); err != nil {
					return err
				}
				continue
			}
			nameStart := i + 1
			j := nameStart
			for j < len(data) && data[j] != '}' {
				j++
			}
			if j >= len(data) {
				// No closing brace.
				if _, err := sink.Fail(start, len(data), "malformed \\N character escape"); err != nil {
					return err
				}
				i = len(data)
				continue
			}
			if j == nameStart {
				// Empty name: the span stops before the closing brace,
				// which is decoded on the next iteration.
				if _, err := sink.Fail(start, j, "malformed \\N character escape"); err != nil {
					return err
				}
				i = j
				continue
			}
			if cp, ok := lookupUnicodeCharacterName(data[nameStart:j]); ok {
				sink.Out = append(sink.Out, cp)
				i = j + 1
				continue
			}
			if _, err := sink.Fail(start, j+1, "unknown Unicode character name"); err != nil {
				return err
			}
			i = j + 1
		default:
			// 3.12+ keeps an unknown escape as-is (DeprecationWarning).
			sink.Out = append(sink.Out, '\\', rune(c))
		}
	}
	return nil
}

// ---------------------------------------------------------------------------
// raw_unicode_escape

// decodeRawUnicodeEscape is _PyUnicode_DecodeRawUnicodeEscapeStateful with a
// final decode.
func decodeRawUnicodeEscape(data []byte, sink *Sink) error {
	sink.SetCodec("rawunicodeescape")

	i := 0
	for i < len(data) {
		c := data[i]
		i++
		// A backslash at the very end is literal (the incremental decoder
		// would hold it back; the final decoder writes it).
		if c != '\\' || i >= len(data) {
			sink.Out = append(sink.Out, rune(c))
			continue
		}

		start := i - 1
		c = data[i]
		i++
		if c != 'u' && c != 'U' {
			// Everything but \u and \U is literal, backslash included.
			sink.Out = append(sink.Out, '\\', rune(c))
			continue
		}

		count := 4
		reason := "truncated \\uXXXX escape"
		if c == 'U' {
			count = 8
			reason = "truncated \\UXXXXXXXX escape"
		}
		value, next, ok := escapeReadHex(data, i, count)
		if !ok {
			if _, err := sink.Fail(start, next, reason); err != nil {
				return err
			}
			i = next
			continue
		}
		if value > 0x10FFFF {
			if _, err := sink.Fail(start, next, "\\Uxxxxxxxx out of range"); err != nil {
				return err
			}
			i = next
			continue
		}
		sink.Out = append(sink.Out, rune(value))
		i = next
	}
	return nil
}

// handlerName is the errors= spelling CPython puts in a UnicodeError raised
// by a codec that refuses an error handler.
func refusedHandlerName(h Handler) string {
	switch h {
	case Replace:
		return "replace"
	case SurrogateEscape:
		return "surrogateescape"
	case Ignore:
		return "ignore"
	}
	return "strict"
}
