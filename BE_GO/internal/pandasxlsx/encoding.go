package pandasxlsx

import (
	"bytes"
	"errors"
	"io"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pycodecs"
)

// openpyxl without lxml parses a part with expat (xml.etree.ElementTree over
// defusedxml). Expat decodes UTF-8, UTF-16 and the two 8-bit encodings it
// carries itself, and asks pyexpat's unknown-encoding handler for every other
// name: the handler decodes the 256 bytes 0x00..0xFF with
// bytes.decode(name, 'replace'), refuses a codec that does not map them to one
// character each ("multi-byte encodings are not supported", a ValueError) and
// builds expat's single-byte map from the result, where a byte that decoded to
// U+FFFD is not a character of the document. internal/pycodecs carries every
// text codec of the registry, so mapEncoding decodes the same 256 bytes with
// it.
//
// The encodings this file models, named by the constants below: the bytes a
// part starts with (a BOM, or the UTF-16 form of '<?xml') select the encoding
// the XML declaration is read in, and the declaration's name selects the one
// it is decoded with. A declaration that names UTF-8 over a UTF-16 part, a
// UTF-16 form the bytes are not, or an 8-bit encoding over a UTF-16 part, is
// the document expat refuses with XML_ERROR_INCORRECT_ENCODING (for the 8-bit
// name: the part after the declaration is NUL bytes under that encoding).

// The encodings a part is decoded from. encUTF16 is the declared name "UTF-16"
// in either byte order.
const (
	encUTF8 = iota
	encUTF16LE
	encUTF16BE
	encLatin1
	encASCII
	encMap
	encUTF16 = -2
	// encInvalid is the BOM of a UTF-32 part, which expat has no decoder for:
	// it reads the first code unit as a NUL character and refuses the part.
	encInvalid = -1
)

// prologLimit bounds the bytes read to find a part's BOM and XML declaration.
// A declaration longer than this is ErrInvalid; Excel, LibreOffice and
// openpyxl write fewer than a hundred bytes.
const prologLimit = 1 << 16

// xmlEncoding is the plan of one part: the UTF-8 bytes to emit first (the XML
// declaration rewritten to UTF-8, or nothing) and how the bytes from prefix on
// are decoded.
type xmlEncoding struct {
	kind   int
	table  [256]rune // encMap, encLatin1 and encASCII: -1 marks a byte expat refuses
	head   []byte    // UTF-8 bytes to emit before the body
	prefix int       // the part bytes the prologue read
}

// xmlContent rewrites one XML part to UTF-8 as expat decodes it: the BOM is
// dropped, the XML declaration's encoding name is rewritten to UTF-8, and the
// bytes after the declaration are decoded with the declared encoding. The
// returned reader yields the part, then the rest of r.
func xmlContent(r io.Reader) (io.Reader, error) {
	prolog := make([]byte, 0, 4096)
	chunk := make([]byte, 4096)
	for len(prolog) < prologLimit {
		n, err := r.Read(chunk)
		prolog = append(prolog, chunk[:n]...)
		if err != nil {
			if !errors.Is(err, io.EOF) {
				return nil, ErrInvalid
			}
			break
		}
	}
	plan, err := prologEncoding(prolog)
	if err != nil {
		return nil, err
	}
	stream := io.MultiReader(bytes.NewReader(prolog[plan.prefix:]), r)
	var body io.Reader
	switch plan.kind {
	case encUTF8:
		body = stream
	case encUTF16LE, encUTF16BE:
		body = &utf16Reader{order: plan.kind, r: stream}
	default:
		body = &mapReader{table: plan.table, r: stream}
	}
	return io.MultiReader(bytes.NewReader(plan.head), body), nil
}

// prologEncoding is the encoding plan of the part's first bytes.
func prologEncoding(prolog []byte) (xmlEncoding, error) {
	skip, detected := bomEncoding(prolog)
	switch detected {
	case encUTF16LE, encUTF16BE:
		return utf16Prolog(prolog, skip, detected)
	case encInvalid:
		return xmlEncoding{}, ErrInvalid
	}
	decl, found, err := parseDeclaration(prolog[skip:])
	if err != nil {
		return xmlEncoding{}, err
	}
	plan := xmlEncoding{kind: encUTF8, prefix: len(prolog)}
	if !found {
		// No declaration: the part is UTF-8 from its first byte (expat still
		// refuses a declaration that opens after a comment or whitespace).
		if misplacedDeclaration(prolog[skip:]) {
			return xmlEncoding{}, ErrInvalid
		}
		plan.prefix = skip
		return plan, nil
	}
	end := skip + decl.end
	plan.prefix = end
	if !decl.hasEncoding {
		plan.head = declarationText(decl)
		return plan, nil
	}
	kind, table, err := declaredEncoding(decl.encoding)
	if err != nil {
		return xmlEncoding{}, err
	}
	if kind == encUTF16 || kind == encUTF16LE || kind == encUTF16BE {
		return xmlEncoding{}, ErrInvalid // the part's first bytes are not UTF-16
	}
	plan.kind, plan.table = kind, table
	plan.head = declarationText(decl)
	return plan, nil
}

// utf16Prolog is prologEncoding for a part whose BOM (or UTF-16 form of
// '<?xml') says UTF-16.
func utf16Prolog(prolog []byte, skip, detected int) (xmlEncoding, error) {
	runes, offsets := utf16Runes(prolog[skip:], detected)
	// The declaration is ASCII; a non-ASCII rune ends it.
	ascii := make([]byte, 0, len(runes))
	for _, r := range runes {
		if r >= utf8.RuneSelf {
			break
		}
		ascii = append(ascii, byte(r))
	}
	decl, found, err := parseDeclaration(ascii)
	if err != nil {
		return xmlEncoding{}, err
	}
	plan := xmlEncoding{kind: detected}
	if !found {
		// No declaration: the part is UTF-16 from its first code unit.
		for _, r := range runes {
			if r >= 0xD800 && r <= 0xDFFF {
				return xmlEncoding{}, ErrInvalid // a lone surrogate: expat's invalid token
			}
		}
		plan.head = append(plan.head, string(runes)...)
		plan.prefix = skip + offsets[len(runes)]
		return plan, nil
	}
	end := decl.end
	plan.prefix = skip + offsets[end]
	if !decl.hasEncoding {
		plan.head = declarationText(decl)
		return plan, nil
	}
	kind, _, err := declaredEncoding(decl.encoding)
	if err != nil {
		return xmlEncoding{}, err
	}
	if kind != encUTF16 && kind != detected {
		return xmlEncoding{}, ErrInvalid
	}
	plan.head = declarationText(decl)
	return plan, nil
}

// bomEncoding is expat's initScan: the byte order the part starts in, and the
// bytes before the document.
func bomEncoding(data []byte) (skip, encoding int) {
	switch {
	case len(data) >= 4 && data[0] == 0xFF && data[1] == 0xFE && data[2] == 0 && data[3] == 0:
		return 0, encInvalid // UTF-32LE BOM
	case len(data) >= 4 && data[0] == 0 && data[1] == 0 && data[2] == 0xFE && data[3] == 0xFF:
		return 0, encInvalid // UTF-32BE BOM
	case len(data) >= 3 && data[0] == 0xEF && data[1] == 0xBB && data[2] == 0xBF:
		return 3, encUTF8
	case len(data) >= 2 && data[0] == 0xFE && data[1] == 0xFF:
		return 2, encUTF16BE
	case len(data) >= 2 && data[0] == 0xFF && data[1] == 0xFE:
		return 2, encUTF16LE
	case len(data) >= 4 && data[0] == 0x3C && data[1] == 0 && data[2] == 0x3F && data[3] == 0:
		return 0, encUTF16LE
	case len(data) >= 4 && data[0] == 0 && data[1] == 0x3C && data[2] == 0 && data[3] == 0x3F:
		return 0, encUTF16BE
	}
	return 0, encUTF8
}

// declaredEncoding is the encoding a declaration names, for a part that is not
// UTF-16: expat's built-in names (compared case insensitively), or pyexpat's
// unknown-encoding handler.
func declaredEncoding(name string) (int, [256]rune, error) {
	switch strings.ToLower(name) {
	case "utf-8":
		return encUTF8, [256]rune{}, nil
	case "utf-16":
		return encUTF16, [256]rune{}, nil
	case "utf-16le":
		return encUTF16LE, [256]rune{}, nil
	case "utf-16be":
		return encUTF16BE, [256]rune{}, nil
	case "iso-8859-1":
		return encLatin1, latin1Table, nil
	case "us-ascii":
		return encASCII, asciiTable, nil
	}
	return mapEncoding(name)
}

// mapEncoding is pyexpat's PyUnknownEncodingHandler: the codec the name
// resolves to must decode each of the 256 bytes to one character (else the
// ValueError "multi-byte encodings are not supported"), and the character a
// byte decodes to is expat's map entry for it (U+FFFD, the replacement, is no
// character).
func mapEncoding(name string) (int, [256]rune, error) {
	codec, ok := codecNames[normalizeCodecName(name)]
	if !ok {
		return 0, [256]rune{}, ErrInvalid // codecs.lookup raises LookupError
	}
	decoded, err, ok := pycodecs.Decode(codec, byteRamp, pycodecs.Replace)
	if !ok || err != nil || len(decoded) != 256 {
		return 0, [256]rune{}, ErrInvalid
	}
	var table [256]rune
	for index, r := range decoded {
		if r == utf8.RuneError {
			table[index] = -1
			continue
		}
		table[index] = r
	}
	return encMap, table, nil
}

// normalizeCodecName is encodings.normalize_encoding of a lowered name: runs
// of characters other than letters, digits and '.' become one '_'.
func normalizeCodecName(name string) string {
	var normalized strings.Builder
	punct := false
	for _, r := range strings.ToLower(name) {
		if r < utf8.RuneSelf && (r >= 'a' && r <= 'z' || r >= '0' && r <= '9' || r == '.') {
			if punct && normalized.Len() > 0 {
				normalized.WriteByte('_')
			}
			normalized.WriteRune(r)
			punct = false
			continue
		}
		punct = true
	}
	return normalized.String()
}

// byteRamp is the 256 bytes pyexpat's unknown-encoding handler decodes.
var byteRamp = func() []byte {
	ramp := make([]byte, 256)
	for index := range ramp {
		ramp[index] = byte(index)
	}
	return ramp
}()

// latin1Table is expat's ISO-8859-1 encoding: every byte is its code point.
var latin1Table = func() [256]rune {
	var table [256]rune
	for index := range table {
		table[index] = rune(index)
	}
	return table
}()

// asciiTable is expat's US-ASCII encoding: the bytes below 0x80 only.
var asciiTable = func() [256]rune {
	var table [256]rune
	for index := range table {
		table[index] = rune(index)
		if index >= 0x80 {
			table[index] = -1
		}
	}
	return table
}()

// declaration is the XML declaration at the start of a part: where it ends
// and the pseudo-attributes expat accepts, in expat's order (version, then an
// optional encoding, then an optional standalone).
type declaration struct {
	end           int
	encoding      string
	hasEncoding   bool
	standalone    string
	hasStandalone bool
}

// declarationText is the declaration as UTF-8 for encoding/xml: the version is
// always "1.0" (expat also reads 1.1, encoding/xml only 1.0) and the encoding,
// when the part has one, is UTF-8, which the bytes after the declaration now
// are.
func declarationText(decl declaration) []byte {
	text := []byte(`<?xml version="1.0"`)
	if decl.hasEncoding {
		text = append(text, ` encoding="UTF-8"`...)
	}
	if decl.hasStandalone {
		text = append(text, ` standalone="`...)
		text = append(text, decl.standalone...)
		text = append(text, '"')
	}
	return append(text, "?>"...)
}

// parseDeclaration is expat's XML declaration grammar over ASCII text:
// '<?xml' VersionInfo EncodingDecl? SDDecl? S? '?>'. found is false where the
// text does not start a declaration; text that starts one expat cannot parse
// is ErrInvalid.
func parseDeclaration(text []byte) (declaration, bool, error) {
	var decl declaration
	if !bytes.HasPrefix(text, []byte("<?xml")) {
		return decl, false, nil
	}
	at := len("<?xml")
	if at >= len(text) {
		return decl, false, ErrInvalid
	}
	if !isXMLSpace(text[at]) && text[at] != '?' {
		return decl, false, nil // '<?xml-stylesheet': a processing instruction
	}
	next := func() (name string, value string, err error) {
		for at < len(text) && isXMLSpace(text[at]) {
			at++
		}
		if at >= len(text) {
			return "", "", ErrInvalid
		}
		if text[at] == '?' {
			if at+1 >= len(text) || text[at+1] != '>' {
				return "", "", ErrInvalid
			}
			decl.end = at + 2
			return "", "", errEndDeclaration
		}
		start := at
		for at < len(text) && isPseudoNameByte(text[at]) {
			at++
		}
		if at == start {
			return "", "", ErrInvalid
		}
		name = string(text[start:at])
		for at < len(text) && isXMLSpace(text[at]) {
			at++
		}
		if at >= len(text) || text[at] != '=' {
			return "", "", ErrInvalid
		}
		at++
		for at < len(text) && isXMLSpace(text[at]) {
			at++
		}
		if at >= len(text) || text[at] != '"' && text[at] != '\'' {
			return "", "", ErrInvalid
		}
		quote := text[at]
		at++
		start = at
		for at < len(text) && text[at] != quote {
			if text[at] == '<' {
				return "", "", ErrInvalid
			}
			at++
		}
		if at >= len(text) {
			return "", "", ErrInvalid
		}
		value = string(text[start:at])
		at++
		return name, value, nil
	}
	name, value, err := next()
	if err != nil && !errors.Is(err, errEndDeclaration) {
		return decl, false, err
	}
	if name != "version" || !isVersionNumber(value) {
		return decl, false, ErrInvalid // expat: version expected
	}
	name, value, err = next()
	if errors.Is(err, errEndDeclaration) {
		return decl, true, nil
	}
	if err != nil {
		return decl, false, err
	}
	if name == "encoding" {
		decl.hasEncoding, decl.encoding = true, value
		if name, value, err = next(); errors.Is(err, errEndDeclaration) {
			return decl, true, nil
		} else if err != nil {
			return decl, false, err
		}
	}
	if name == "standalone" {
		if value != "yes" && value != "no" {
			return decl, false, ErrInvalid
		}
		decl.hasStandalone, decl.standalone = true, value
		if _, _, err = next(); errors.Is(err, errEndDeclaration) {
			return decl, true, nil
		} else if err != nil {
			return decl, false, err
		}
	}
	return decl, false, ErrInvalid // an attribute expat does not accept
}

// errEndDeclaration ends parseDeclaration's pseudo-attribute loop at '?>'.
var errEndDeclaration = errors.New("pandasxlsx: end of the XML declaration")

// isVersionNumber is expat's VersionNum: '1.' and digits.
func isVersionNumber(value string) bool {
	if len(value) < 3 || value[0] != '1' || value[1] != '.' {
		return false
	}
	for index := 2; index < len(value); index++ {
		if value[index] < '0' || value[index] > '9' {
			return false
		}
	}
	return true
}

func isXMLSpace(c byte) bool { return c == ' ' || c == '\t' || c == '\r' || c == '\n' }

func isPseudoNameByte(c byte) bool {
	return c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' ||
		c == '_' || c == ':' || c == '-' || c == '.'
}

// misplacedDeclaration reports whether the part opens with a declaration that
// is not at its start: expat reads one only as the very first thing ("XML or
// text declaration not at start of entity"), so whitespace, a comment or a
// processing instruction before it is an error. The scan stops at the first
// element, so a declaration-shaped attribute value or text later in the part
// is not one.
func misplacedDeclaration(data []byte) bool {
	at := 0
	for {
		for at < len(data) && isXMLSpace(data[at]) {
			at++
		}
		if bytes.HasPrefix(data[at:], []byte("<!--")) {
			end := bytes.Index(data[at+4:], []byte("-->"))
			if end < 0 {
				return false
			}
			at += 4 + end + 3
			continue
		}
		if bytes.HasPrefix(data[at:], []byte("<?")) {
			end := bytes.Index(data[at+2:], []byte("?>"))
			if end < 0 {
				return false
			}
			target := data[at+2:]
			if len(target) >= 3 && bytes.HasPrefix(target, []byte("xml")) &&
				(len(target) == 3 || isXMLSpace(target[3]) || target[3] == '?') {
				return true
			}
			at += 2 + end + 2
			continue
		}
		return false
	}
}

// utf16Runes decodes a UTF-16 part's prolog. offsets[i] is the byte offset of
// rune i and offsets[len(runes)] the bytes decoded: an incomplete final code
// unit is left for the stream.
func utf16Runes(data []byte, order int) ([]rune, []int) {
	read := func(at int) rune {
		if order == encUTF16LE {
			return rune(data[at]) | rune(data[at+1])<<8
		}
		return rune(data[at])<<8 | rune(data[at+1])
	}
	runes := make([]rune, 0, len(data)/2)
	offsets := make([]int, 0, len(data)/2+1)
	for at := 0; at+1 < len(data); {
		offsets = append(offsets, at)
		unit := read(at)
		if unit >= 0xD800 && unit <= 0xDBFF && at+3 < len(data) {
			if low := read(at + 2); low >= 0xDC00 && low <= 0xDFFF {
				runes = append(runes, 0x10000+(unit-0xD800)<<10+(low-0xDC00))
				at += 4
				continue
			}
		}
		runes = append(runes, unit)
		at += 2
	}
	offsets = append(offsets, len(data)-len(data)%2)
	return runes, offsets
}

// utf16Reader decodes the rest of a UTF-16 part to UTF-8.
type utf16Reader struct {
	order   int
	r       io.Reader
	pending []byte
	left    []byte
	in      [1024]byte
	err     error
}

func (u *utf16Reader) Read(p []byte) (int, error) {
	for len(u.pending) == 0 {
		if u.err != nil {
			return 0, u.err
		}
		n, err := u.r.Read(u.in[:])
		chunk := append(u.left, u.in[:n]...)
		u.left = nil
		at := 0
		for at+1 < len(chunk) {
			unit := u.unit(chunk[at:])
			if unit >= 0xD800 && unit <= 0xDBFF {
				if at+3 >= len(chunk) {
					break // a surrogate pair may continue in the next read
				}
				low := u.unit(chunk[at+2:])
				if low >= 0xDC00 && low <= 0xDFFF {
					u.pending = utf8.AppendRune(u.pending, 0x10000+(unit-0xD800)<<10+(low-0xDC00))
					at += 4
					continue
				}
				u.err = ErrInvalid // a high surrogate no low surrogate follows
				break
			}
			if unit >= 0xDC00 && unit <= 0xDFFF {
				u.err = ErrInvalid // a lone low surrogate
				break
			}
			u.pending = utf8.AppendRune(u.pending, rune(unit))
			at += 2
		}
		u.left = append(u.left, chunk[at:]...)
		if err != nil && u.err == nil {
			if len(u.left) != 0 {
				u.err = ErrInvalid // an incomplete final code unit
			} else {
				u.err = err
			}
		}
		if len(u.pending) == 0 && u.err != nil {
			return 0, u.err
		}
	}
	n := copy(p, u.pending)
	u.pending = u.pending[n:]
	return n, nil
}

func (u *utf16Reader) unit(data []byte) rune {
	if u.order == encUTF16LE {
		return rune(data[0]) | rune(data[1])<<8
	}
	return rune(data[0])<<8 | rune(data[1])
}

// mapReader decodes the rest of a part with expat's single-byte map.
type mapReader struct {
	table   [256]rune
	r       io.Reader
	pending []byte
	in      [1024]byte
	err     error
}

func (m *mapReader) Read(p []byte) (int, error) {
	for len(m.pending) == 0 {
		if m.err != nil {
			return 0, m.err
		}
		n, err := m.r.Read(m.in[:])
		for _, b := range m.in[:n] {
			r := m.table[b]
			if r < 0 {
				m.err = ErrInvalid // a byte that is no character of the part
				break
			}
			m.pending = utf8.AppendRune(m.pending, r)
		}
		if m.err == nil && err != nil {
			m.err = err
		}
		if len(m.pending) == 0 && m.err != nil {
			return 0, m.err
		}
	}
	n := copy(p, m.pending)
	m.pending = m.pending[n:]
	return n, nil
}
