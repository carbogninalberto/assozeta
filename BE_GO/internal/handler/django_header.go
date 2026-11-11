package handler

import (
	"errors"
	"fmt"
	"net/textproto"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type pythonCodec struct {
	Name string
	Text bool
}

// djangoHeaderOutcome says what django.utils.http.parse_header_parameters does
// with a header line.
type djangoHeaderOutcome int

const (
	headerParsed    djangoHeaderOutcome = iota
	headerInvalid                       // a ValueError: parse_boundary_stream skips the line
	headerNotPorted                     // a charset whose decoder is not ported
)

// pyIndex is str.find(sub, start, end) on code points.
func pyIndex(s []rune, sub rune, start, end int) int {
	start, end = pySlice(len(s), start, end)
	for i := start; i < end; i++ {
		if s[i] == sub {
			return i
		}
	}
	return -1
}

// pyCount is str.count(sub, start, end) for a one- or two-character sub.
func pyCount(s []rune, sub string, start, end int) int {
	start, end = pySlice(len(s), start, end)
	if start > end {
		return 0
	}
	return strings.Count(string(s[start:end]), sub)
}

func pySlice(length, start, end int) (int, int) {
	norm := func(i int) int {
		if i < 0 {
			if i += length; i < 0 {
				i = 0
			}
		}
		if i > length {
			i = length
		}
		return i
	}
	return norm(start), norm(end)
}

// pyParseParam is email.message._parseparam.
func pyParseParam(line string) []string {
	s := []rune(";" + line)
	var plist []string
	start := 0
	for pyIndex(s, ';', start, len(s)) == start {
		start++
		end := pyIndex(s, ';', start, len(s))
		ind, diff := start, 0
		for end > 0 {
			diff += pyCount(s, `"`, ind, end) - pyCount(s, `\"`, ind, end)
			if diff%2 == 0 {
				break
			}
			end, ind = ind, pyIndex(s, ';', end+1, len(s))
		}
		if end < 0 {
			end = len(s)
		}
		var piece string
		if i := pyIndex(s, '=', start, end); i == -1 {
			piece = string(s[start:end])
		} else {
			piece = strings.ToLower(strings.TrimRightFunc(string(s[start:i]), pyIsSpace)) + "=" +
				strings.TrimLeftFunc(string(s[i+1:end]), pyIsSpace)
		}
		plist = append(plist, strings.TrimFunc(piece, pyIsSpace))
		start = end
	}
	return plist
}

func pyIsSpace(r rune) bool {
	return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f
}

// pyUnquote is email.utils.unquote.
func pyUnquote(text string) string {
	if runes := []rune(text); len(runes) > 1 {
		if strings.HasPrefix(text, `"`) && strings.HasSuffix(text, `"`) {
			return strings.ReplaceAll(strings.ReplaceAll(string(runes[1:len(runes)-1]), `\\`, `\`), `\"`, `"`)
		}
		if strings.HasPrefix(text, "<") && strings.HasSuffix(text, ">") {
			return string(runes[1 : len(runes)-1])
		}
	}
	return text
}

// pyQuote is email.utils.quote.
func pyQuote(text string) string {
	return strings.ReplaceAll(strings.ReplaceAll(text, `\`, `\\`), `"`, `\"`)
}

// rfc2231Name is rfc2231_continuation: ^(?P<name>\w+)\*((?P<num>[0-9]+)\*?)?$ (ASCII).
func rfc2231Name(name string) (base string, num int, hasNum, ok bool) {
	word := func(c byte) bool {
		return c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '_'
	}
	i := 0
	for i < len(name) && word(name[i]) {
		i++
	}
	if i == 0 || i >= len(name) || name[i] != '*' {
		return "", 0, false, false
	}
	base, rest := name[:i], name[i+1:]
	if rest == "" {
		return base, 0, false, true
	}
	digits := rest
	if strings.HasSuffix(rest, "*") {
		digits = rest[:len(rest)-1]
	}
	if digits == "" {
		return "", 0, false, false
	}
	value := 0
	for j := 0; j < len(digits); j++ {
		if digits[j] < '0' || digits[j] > '9' {
			return "", 0, false, false
		}
		if value < 1<<40 {
			value = value*10 + int(digits[j]-'0')
		}
	}
	return base, value, true, true
}

type rfc2231Part struct {
	num     int
	hasNum  bool
	value   string
	encoded bool
}

// pyPercentLatin1 is urllib.parse.unquote(text, encoding='latin-1'): every
// valid %XX becomes the character with that code.
func pyPercentLatin1(text string) string {
	hex := func(c rune) int {
		switch {
		case c >= '0' && c <= '9':
			return int(c - '0')
		case c >= 'a' && c <= 'f':
			return int(c-'a') + 10
		case c >= 'A' && c <= 'F':
			return int(c-'A') + 10
		}
		return -1
	}
	runes := []rune(text)
	var out strings.Builder
	for i := 0; i < len(runes); i++ {
		if runes[i] == '%' && i+2 < len(runes) && hex(runes[i+1]) >= 0 && hex(runes[i+2]) >= 0 {
			out.WriteRune(rune(hex(runes[i+1])<<4 | hex(runes[i+2])))
			i += 2
			continue
		}
		out.WriteRune(runes[i])
	}
	return out.String()
}

// djangoParseHeader is django.utils.http.parse_header_parameters as
// parse_boundary_stream calls it: the line's main value (lower-cased) and its
// parameters.
func djangoParseHeader(line string) (string, map[string]string, djangoHeaderOutcome) {
	if line == "" {
		return "", map[string]string{}, headerParsed
	}
	if !strings.Contains(line, ";") {
		return strings.ToLower(strings.TrimFunc(line, pyIsSpace)), map[string]string{}, headerParsed
	}
	pieces := pyParseParam(line)
	type pair struct{ name, value string }
	params := make([]pair, len(pieces))
	for i, piece := range pieces {
		if name, value, found := strings.Cut(piece, "="); found {
			params[i] = pair{strings.TrimFunc(name, pyIsSpace), strings.TrimFunc(value, pyIsSpace)}
		} else {
			params[i] = pair{strings.TrimFunc(piece, pyIsSpace), ""}
		}
	}
	// email.utils.decode_params
	type decoded struct {
		name, value        string
		charset, extension *string
		tuple              bool
	}
	var out []decoded
	continuations := map[string][]rfc2231Part{}
	var order []string
	for _, p := range params[1:] {
		encoded := strings.HasSuffix(p.name, "*")
		value := pyUnquote(p.value)
		if base, num, hasNum, ok := rfc2231Name(p.name); ok {
			if _, seen := continuations[base]; !seen {
				order = append(order, base)
			}
			continuations[base] = append(continuations[base], rfc2231Part{num, hasNum, value, encoded})
			continue
		}
		out = append(out, decoded{name: p.name, value: pyUnquote(`"` + pyQuote(value) + `"`)})
	}
	for _, name := range order {
		parts := continuations[name]
		hasZero := false
		for _, part := range parts {
			if part.hasNum && part.num == 0 {
				hasZero = true
			}
		}
		var kept []rfc2231Part
		for _, part := range parts {
			if hasZero && !part.hasNum {
				continue
			}
			kept = append(kept, part)
		}
		// Stable sort by number (None counts as 0 when there is no 0).
		for i := 1; i < len(kept); i++ {
			for j := i; j > 0 && kept[j].num < kept[j-1].num; j-- {
				kept[j], kept[j-1] = kept[j-1], kept[j]
			}
		}
		var joined strings.Builder
		extended := false
		for _, part := range kept {
			text := part.value
			if part.encoded {
				text = pyPercentLatin1(text)
				extended = true
			}
			joined.WriteString(text)
		}
		value := pyQuote(joined.String())
		if !extended {
			out = append(out, decoded{name: name, value: pyUnquote(`"` + value + `"`)})
			continue
		}
		// decode_rfc2231: split on the first two quotes.
		var charset, language *string
		text := value
		if split := strings.SplitN(value, "'", 3); len(split) == 3 {
			charset, language, text = &split[0], &split[1], split[2]
		}
		out = append(out, decoded{name: name, value: pyUnquote(`"` + text + `"`), charset: charset, extension: language, tuple: true})
	}
	main := strings.ToLower(params[0].name)
	result := map[string]string{}
	for _, item := range out {
		if item.name == "" {
			continue
		}
		value := item.value
		if item.tuple {
			// collapse_rfc2231_value: the characters are octets; decode them with the charset.
			charset := "us-ascii"
			if item.charset != nil && *item.charset != "" {
				codec, known := lookupPythonCodec(*item.charset)
				if !known {
					return main, nil, headerInvalid
				}
				charset = *item.charset
				if !codec.Text {
					result[item.name] = pyUnquote(value)
					continue
				}
			} else if item.charset != nil {
				// str(raw, '') is a LookupError: unquote(text).
				result[item.name] = pyUnquote(value)
				continue
			}
			decodedText, outcome := decodeRawUnicodeEscape(value, charset)
			if outcome != headerParsed {
				return main, nil, outcome
			}
			value = decodedText
		}
		result[item.name] = value
	}
	return main, result, headerParsed
}

func lookupPythonCodec(name string) (pythonCodec, bool) {
	var normalized strings.Builder
	punct := false
	for _, r := range strings.ToLower(name) {
		if r < 0x80 && (r >= 'a' && r <= 'z' || r >= '0' && r <= '9' || r == '.') {
			if punct && normalized.Len() > 0 {
				normalized.WriteByte('_')
			}
			normalized.WriteRune(r)
			punct = false
		} else {
			punct = true
		}
	}
	key := normalized.String()
	for _, candidate := range []string{key, strings.ReplaceAll(key, "_", "-"), strings.ReplaceAll(key, ".", "_")} {
		if codec, ok := pythonCodecs[candidate]; ok {
			return codec, true
		}
	}
	return pythonCodec{}, false
}

// decodeRawUnicodeEscape is str(bytes(text, 'raw-unicode-escape'), charset,
// 'replace') for the supported decoders.
func decodeRawUnicodeEscape(text, charset string) (string, djangoHeaderOutcome) {
	var raw []byte
	for _, r := range text {
		switch {
		case r <= 0xff:
			raw = append(raw, byte(r))
		case r <= 0xffff:
			raw = append(raw, fmt.Sprintf(`\u%04x`, r)...)
		default:
			raw = append(raw, fmt.Sprintf(`\U%08x`, r)...)
		}
	}
	codec, _ := lookupPythonCodec(charset)
	switch codec.Name {
	case "utf-8":
		return dto.PythonUTF8Replace(string(raw)), headerParsed
	case "ascii":
		var out strings.Builder
		for _, b := range raw {
			if b < 0x80 {
				out.WriteByte(b)
			} else {
				out.WriteRune('�')
			}
		}
		return out.String(), headerParsed
	case "iso8859-1":
		var out strings.Builder
		for _, b := range raw {
			out.WriteRune(rune(b))
		}
		return out.String(), headerParsed
	case "cp1252":
		var out strings.Builder
		for _, b := range raw {
			switch {
			case b < 0x80 || b >= 0xa0:
				out.WriteRune(rune(b))
			case cp1252High[b-0x80] == 0:
				out.WriteRune('\ufffd')
			default:
				out.WriteRune(cp1252High[b-0x80])
			}
		}
		return out.String(), headerParsed
	case "undefined":
		return "", headerInvalid
	}
	return "", headerNotPorted
}

// cp1252High is windows-1252 for the bytes 0x80..0x9f (0 where undefined).
var cp1252High = [32]rune{0x20ac, 0, 0x201a, 0x0192, 0x201e, 0x2026, 0x2020, 0x2021, 0x02c6, 0x2030, 0x0160, 0x2039, 0x0152, 0, 0x017d, 0,
	0, 0x2018, 0x2019, 0x201c, 0x201d, 0x2022, 0x2013, 0x2014, 0x02dc, 0x2122, 0x0161, 0x203a, 0x0153, 0, 0x017e, 0x0178}

type djangoPart struct {
	disposition    map[string]string
	hasDisposition bool
	base64         bool
}

// djangoPartHeaders is the header loop of parse_boundary_stream for one part:
// each line is decoded as UTF-8 and parsed, a line that raises a ValueError is
// skipped, and the last line of a name wins.
func djangoPartHeaders(header textproto.MIMEHeader) (djangoPart, djangoHeaderOutcome) {
	var part djangoPart
	for key, values := range header {
		lower := strings.ToLower(key)
		if lower != "content-disposition" && lower != "content-transfer-encoding" {
			continue
		}
		for _, value := range values {
			line := key + ": " + value
			if !utf8.ValidString(line) {
				continue
			}
			main, params, outcome := djangoParseHeader(line)
			if outcome == headerNotPorted {
				return part, outcome
			}
			name, rest, found := strings.Cut(main, ":")
			if outcome != headerParsed || !found {
				continue
			}
			switch name {
			case "content-disposition":
				part.disposition, part.hasDisposition = params, true
			case "content-transfer-encoding":
				part.base64 = strings.TrimFunc(rest, pyIsSpace) == "base64"
			}
		}
	}
	return part, headerParsed
}

func stripASCIISpace(data []byte) []byte {
	out := make([]byte, 0, len(data))
	for _, b := range data {
		if b != ' ' && b != '\t' && b != '\n' && b != '\r' && b != '\v' && b != '\f' {
			out = append(out, b)
		}
	}
	return out
}

var errPyBase64 = errors.New("binascii.Error")

// pyBase64Decode is base64.b64decode(data) (binascii.a2b_base64, not strict):
// characters outside the alphabet are skipped, a padding sequence ends the
// input and an incomplete group is an error.
func pyBase64Decode(data []byte) ([]byte, error) {
	const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
	var table [256]int8
	for i := range table {
		table[i] = -1
	}
	for i := 0; i < len(alphabet); i++ {
		table[alphabet[i]] = int8(i)
	}
	out := make([]byte, 0, len(data)/4*3)
	quad, pads, left := 0, 0, 0
	for _, c := range data {
		if c == '=' {
			// pads only counts once the group holds two characters.
			if quad >= 2 {
				pads++
			}
			continue
		}
		value := table[c]
		if value < 0 {
			continue
		}
		pads = 0
		switch quad {
		case 0:
			quad, left = 1, int(value)
		case 1:
			out = append(out, byte(left<<2|int(value)>>4))
			quad, left = 2, int(value)&0xf
		case 2:
			out = append(out, byte(left<<4|int(value)>>2))
			quad, left = 3, int(value)&0x3
		case 3:
			out = append(out, byte(left<<6|int(value)))
			quad, left = 0, 0
		}
	}
	if quad != 0 && !(quad >= 2 && quad+pads >= 4) {
		return nil, errPyBase64
	}
	return out, nil
}

// djangoHeaderParameters is the parameter map of a Content-Disposition value;
// ok is false for a line parse_header_parameters rejects or this port does not
// decode.
func djangoHeaderParameters(line string) (map[string]string, bool) {
	_, params, outcome := djangoParseHeader("content-disposition: " + line)
	return params, outcome == headerParsed
}
