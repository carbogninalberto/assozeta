package smtpsend

// The decoders CPython's bytes.decode runs for the RFC 2047 charsets this
// package carries, beyond UTF-8, US-ASCII and ISO-8859-1: the 8-bit charmap
// codecs, the UTF-16 and UTF-32 families and utf-8-sig.
//
// email._encoded_words.decode decodes an encoded word's bytes twice: the
// strict str(bstring, charset) first, and then, when that raises
// UnicodeDecodeError, str(bstring, charset, 'surrogateescape'). The second
// pass is what decides the text, and the cases below reproduce it as CPython
// 3.13.16 measures it:
//
//   - An 8-bit charmap codec maps every defined byte through its table and
//     escapes an undefined byte as U+DC00+byte; an undefined byte below 0x80
//     makes the handler re-raise, and the UnicodeDecodeError then leaves
//     _encoded_words.decode.
//   - The UTF-16 and UTF-32 decoders report a byte range for each error, and
//     the handler escapes the bytes >= 0x80 that begin that range, resuming
//     the decode just after them. The range's remaining bytes are decoded
//     again from there, which can leave the decoder between two code units.
//     A range that starts with a byte below 0x80 is not handled at all and
//     the UnicodeDecodeError escapes.
//   - utf-16 and utf-32 read a leading byte order mark and otherwise decode in
//     the native order (little-endian on the production platforms); their -le
//     and -be names fix the order and decode a mark as a character. utf-8-sig
//     drops one leading UTF-8 mark.
//
// A UnicodeDecodeError escaping _encoded_words.decode is a ValueError, so
// get_encoded_word catches it and raises _InvalidEwError, an
// email.errors.HeaderParseError: the encoded word is invalid, not unported,
// and the callers fall back exactly as they do for a malformed word.

import (
	"encoding/binary"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// surrogateescapePrefix is the surrogateescape handling of the failing byte
// range raw[start:end]: the escapes of its leading bytes >= 0x80, appended to
// out, and where the decode resumes (start plus their count). ok is false
// where Python's handler re-raises, which is whenever the range starts below
// 0x80.
func surrogateescapePrefix(out []rune, raw []byte, start, end int) (runes []rune, resume int, ok bool) {
	resume = start
	for resume < end && raw[resume] >= 0x80 {
		out = append(out, 0xDC00+rune(raw[resume]))
		resume++
	}
	return out, resume, resume > start
}

// decodeSingleByteSurrogateescape is b.decode(codec, 'surrogateescape') for a
// byte -> code point table codec: U+FFFD entries are the bytes CPython leaves
// undefined, and each one is its own failing range.
func decodeSingleByteSurrogateescape(raw []byte, table *[256]rune) (string, error) {
	runes := make([]rune, 0, len(raw))
	for _, b := range raw {
		code := table[b]
		if code == 0xFFFD {
			if b < 0x80 {
				return "", errInvalidEncodedWord
			}
			code = 0xDC00 + rune(b)
		}
		runes = append(runes, code)
	}
	return pystr.String(runes), nil
}

// decodeUTF16Surrogateescape is b.decode(codec, 'surrogateescape') for the
// utf-16 family. bom is true for 'utf-16', which reads a byte order mark and
// otherwise decodes in the native order; the other two names fix it. ok is
// false where the decode re-raises.
func decodeUTF16Surrogateescape(raw []byte, order binary.ByteOrder, bom bool) (text string, ok bool) {
	start := 0
	if bom && len(raw) >= 2 {
		switch {
		case raw[0] == 0xFE && raw[1] == 0xFF:
			order, start = binary.BigEndian, 2
		case raw[0] == 0xFF && raw[1] == 0xFE:
			order, start = binary.LittleEndian, 2
		}
	}
	out := make([]rune, 0, len(raw)/2)
	ok = true
	for i := start; i < len(raw) && ok; {
		if len(raw)-i < 2 {
			// Truncated data: the last byte is the failing range.
			out, i, ok = surrogateescapePrefix(out, raw, i, len(raw))
			continue
		}
		unit := order.Uint16(raw[i:])
		switch {
		case unit >= 0xD800 && unit <= 0xDBFF && len(raw)-i >= 4:
			if next := order.Uint16(raw[i+2:]); next >= 0xDC00 && next <= 0xDFFF {
				out = append(out, 0x10000+(rune(unit)-0xD800)<<10+(rune(next)-0xDC00))
				i += 4
				continue
			}
			// Illegal UTF-16 surrogate: the high surrogate's range.
			out, i, ok = surrogateescapePrefix(out, raw, i, i+2)
		case unit >= 0xD800 && unit <= 0xDFFF:
			// A lone low surrogate (illegal encoding), or a high surrogate
			// with no complete code unit behind it (unexpected end of data).
			out, i, ok = surrogateescapePrefix(out, raw, i, i+2)
		default:
			out = append(out, rune(unit))
			i += 2
		}
	}
	if !ok {
		return "", false
	}
	return pystr.String(out), true
}

// decodeUTF32Surrogateescape is b.decode(codec, 'surrogateescape') for the
// utf-32 family.
func decodeUTF32Surrogateescape(raw []byte, order binary.ByteOrder, bom bool) (text string, ok bool) {
	start := 0
	if bom && len(raw) >= 4 {
		switch {
		case raw[0] == 0xFF && raw[1] == 0xFE && raw[2] == 0x00 && raw[3] == 0x00:
			order, start = binary.LittleEndian, 4
		case raw[0] == 0x00 && raw[1] == 0x00 && raw[2] == 0xFE && raw[3] == 0xFF:
			order, start = binary.BigEndian, 4
		}
	}
	out := make([]rune, 0, len(raw)/4)
	ok = true
	for i := start; i < len(raw) && ok; {
		if len(raw)-i < 4 {
			// Truncated data: the remaining bytes are the failing range.
			out, i, ok = surrogateescapePrefix(out, raw, i, len(raw))
			continue
		}
		cp := order.Uint32(raw[i:])
		if cp > 0x10FFFF || cp >= 0xD800 && cp <= 0xDFFF {
			// Code point not in range(0x110000), or in the surrogate range.
			out, i, ok = surrogateescapePrefix(out, raw, i, i+4)
			continue
		}
		out = append(out, rune(cp))
		i += 4
	}
	if !ok {
		return "", false
	}
	return pystr.String(out), true
}

// decodeUTF8SigSurrogateescape is b.decode('utf-8-sig', 'surrogateescape'):
// utf-8 after one leading byte order mark, escaped byte by byte.
func decodeUTF8SigSurrogateescape(raw []byte) string {
	if len(raw) >= 3 && raw[0] == 0xEF && raw[1] == 0xBB && raw[2] == 0xBF {
		raw = raw[3:]
	}
	return decodeUTF8Surrogateescape(raw)
}

// decodedOrInvalid is decodeCharset's answer for a surrogateescape decoder
// that re-raises: Python's UnicodeDecodeError, which get_encoded_word raises
// as _InvalidEwError.
func decodedOrInvalid(text string, ok bool) (string, error) {
	if !ok {
		return "", errInvalidEncodedWord
	}
	return text, nil
}

// ErrEncodedWordCharset refuses an RFC 2047 encoded word in an address whose
// charset is a text codec this package does not carry, by codecs.lookup name:
// the CJK multibyte codecs (big5, big5hkscs, cp932, cp949, cp950,
// euc_jis_2004, euc_jisx0213, euc_jp, euc_kr, gb18030, gb2312, gbk, johab,
// shift_jis, shift_jis_2004, shift_jisx0213), the escape codecs (hz,
// iso2022_jp, iso2022_jp_1, iso2022_jp_2, iso2022_jp_2004, iso2022_jp_3,
// iso2022_jp_ext, iso2022_kr, utf-7), and the codecs that are not a fixed
// byte -> character map (idna, punycode, unicode-escape, raw-unicode-escape).
// Every other text codec of CPython's registry is decoded.
var ErrEncodedWordCharset = refusal.New("smtpsend: encoded-word charset not ported")
