package dto

import (
	"encoding/json"
	"errors"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"golang.org/x/text/unicode/norm"
)

// StaffBoardDocumentMaxBytes bounds Python json.dumps(document), including the
// editor's base64 image data.
const StaffBoardDocumentMaxBytes = 14 * 1024 * 1024

// StaffBoardDocumentError carries Django's exact validation message.
type StaffBoardDocumentError struct{ Message string }

func (e *StaffBoardDocumentError) Error() string { return e.Message }

// ErrStaffBoardDocumentUnhashable mirrors Django's uncaught TypeError when an
// object or array is used as a node or mark type. It is a server error there.
var ErrStaffBoardDocumentUnhashable = errors.New("staff-board document type is unhashable")

var (
	staffBoardNodeTypes = map[string]bool{"doc": true, "paragraph": true, "heading": true, "text": true,
		"hardBreak": true, "bulletList": true, "orderedList": true, "listItem": true, "blockquote": true,
		"codeBlock": true, "horizontalRule": true, "image": true, "taskList": true, "taskItem": true,
		"table": true, "tableRow": true, "tableCell": true, "tableHeader": true, "pageBreak": true}
	staffBoardMarkTypes  = map[string]bool{"bold": true, "italic": true, "underline": true, "strike": true, "code": true, "link": true}
	staffBoardTextBlocks = map[string]bool{"paragraph": true, "heading": true, "listItem": true, "hardBreak": true, "codeBlock": true}
	staffBoardAlignments = map[string]bool{"left": true, "center": true, "right": true, "justify": true}
	staffBoardDataImage  = regexp.MustCompile(`^data:image/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/]+={0,2}$`)
	pythonIPvFuture      = regexp.MustCompile(`^[vV][a-fA-F0-9]+\..+$`)
)

func staffBoardInvalid(message string) error { return &StaffBoardDocumentError{Message: message} }

// ValidateStaffBoardDocument ports communications.staff_board.validate_document
// for values from ParseProfileTablesJSON. It returns the sanitized document in
// Django's key order and the stripped extracted text.
func ValidateStaffBoardDocument(document any) (any, string, error) {
	root, ok := document.(domain.RequestObject)
	if kind, _ := lookupValue(root, "type"); !ok || kind != "doc" {
		return nil, "", staffBoardInvalid("Documento non valido.")
	}
	if len(PythonJSONDumps(document)) > StaffBoardDocumentMaxBytes {
		return nil, "", staffBoardInvalid("Il messaggio con le immagini è troppo grande (massimo 14 MB).")
	}
	v := &staffBoardVisitor{}
	cleaned, err := v.visit(document, 0)
	if err != nil {
		return nil, "", err
	}
	text := pythonStrip(v.text.String())
	if text == "" && v.images == 0 {
		return nil, "", staffBoardInvalid("Scrivi un messaggio o aggiungi un’immagine.")
	}
	if len(pythonCodePoints(text)) > 10000 {
		return nil, "", staffBoardInvalid("Il testo può contenere al massimo 10000 caratteri.")
	}
	return cleaned, text, nil
}

type staffBoardVisitor struct {
	count  int
	images int
	text   strings.Builder
}

func (v *staffBoardVisitor) visit(value any, depth int) (any, error) {
	v.count++
	node, isObject := value.(domain.RequestObject)
	if depth > 30 || v.count > 5000 || !isObject {
		return nil, staffBoardInvalid("Struttura del documento non valida.")
	}
	kind, err := pythonSetMember(node, "type", staffBoardNodeTypes)
	if err != nil {
		return nil, err
	}
	if kind == "" {
		return nil, staffBoardInvalid("Struttura del documento non valida.")
	}
	attrs := domain.RequestObject{}
	if raw, _ := lookupValue(node, "attrs"); pythonTruthy(raw) {
		object, ok := raw.(domain.RequestObject)
		if !ok {
			return nil, staffBoardInvalid("Attributi non validi.")
		}
		attrs = object
	}
	result := domain.RequestObject{{Name: "type", Value: kind}}
	cleaned := domain.RequestObject{}
	if kind == "text" {
		text, ok := lookupValue(node, "text")
		value, isString := text.(string)
		if !ok || !isString {
			return nil, staffBoardInvalid("Testo non valido.")
		}
		result = append(result, domain.RequestField{Name: "text", Value: value})
		v.text.WriteString(value)
	}
	if kind == "image" {
		if src, _ := lookupValue(attrs, "src"); !pythonSafeURL(src, true) {
			return nil, staffBoardInvalid("Usa immagini PNG, JPEG, GIF o WebP, oppure un URL HTTP/HTTPS.")
		}
		for _, key := range []string{"src", "alt", "title"} {
			if value, ok := lookupValue(attrs, key); ok {
				if text, isString := value.(string); isString {
					cleaned = append(cleaned, domain.RequestField{Name: key, Value: text})
				}
			}
		}
		v.images++
	}
	for _, key := range []string{"level", "start", "colspan", "rowspan"} {
		value, present := lookupValue(attrs, key)
		if !present {
			continue
		}
		limit := int64(1000)
		if key == "level" {
			limit = 6
		}
		number, isNumber := value.(json.Number)
		integer, ok := new(big.Int), false
		if isNumber && !strings.ContainsAny(string(number), ".eE") {
			_, ok = integer.SetString(string(number), 10)
		}
		if !ok || integer.Cmp(big.NewInt(1)) < 0 || integer.Cmp(big.NewInt(limit)) > 0 {
			return nil, staffBoardInvalid("Dimensione non valida.")
		}
		cleaned = append(cleaned, domain.RequestField{Name: key, Value: json.Number(integer.String())})
	}
	if align, _ := lookupValue(attrs, "textAlign"); staffBoardAlignments[stringValue(align)] {
		cleaned = append(cleaned, domain.RequestField{Name: "textAlign", Value: align})
	}
	if kind == "taskItem" {
		checked, _ := lookupValue(attrs, "checked")
		cleaned = append(cleaned, domain.RequestField{Name: "checked", Value: checked == true})
	}
	if len(cleaned) > 0 {
		result = append(result, domain.RequestField{Name: "attrs", Value: cleaned})
	}
	rawMarks, present := lookupValue(node, "marks")
	if !present {
		rawMarks = []any{}
	}
	marks, ok := rawMarks.([]any)
	if !ok || len(marks) > 10 {
		return nil, staffBoardInvalid("Formattazione non valida.")
	}
	if len(marks) > 0 {
		cleanedMarks := make([]any, 0, len(marks))
		for _, raw := range marks {
			mark, isObject := raw.(domain.RequestObject)
			if !isObject {
				return nil, staffBoardInvalid("Formattazione non valida.")
			}
			markType, err := pythonSetMember(mark, "type", staffBoardMarkTypes)
			if err != nil {
				return nil, err
			}
			if markType == "" {
				return nil, staffBoardInvalid("Formattazione non valida.")
			}
			cleanMark := domain.RequestObject{{Name: "type", Value: markType}}
			if markType == "link" {
				var href any
				if markAttrs, _ := lookupValue(mark, "attrs"); pythonTruthy(markAttrs) {
					if object, isObject := markAttrs.(domain.RequestObject); isObject {
						href, _ = lookupValue(object, "href")
					}
				}
				if !pythonSafeURL(href, false) {
					return nil, staffBoardInvalid("Link non valido.")
				}
				cleanMark = append(cleanMark, domain.RequestField{Name: "attrs", Value: domain.RequestObject{
					{Name: "href", Value: href}, {Name: "target", Value: "_blank"}, {Name: "rel", Value: "noopener noreferrer"}}})
			}
			cleanedMarks = append(cleanedMarks, cleanMark)
		}
		result = append(result, domain.RequestField{Name: "marks", Value: cleanedMarks})
	}
	rawChildren, present := lookupValue(node, "content")
	if !present {
		rawChildren = []any{}
	}
	children, ok := rawChildren.([]any)
	if !ok {
		return nil, staffBoardInvalid("Contenuto non valido.")
	}
	if len(children) > 0 {
		content := make([]any, 0, len(children))
		for _, child := range children {
			cleanedChild, err := v.visit(child, depth+1)
			if err != nil {
				return nil, err
			}
			content = append(content, cleanedChild)
		}
		result = append(result, domain.RequestField{Name: "content", Value: content})
	}
	if staffBoardTextBlocks[kind] {
		v.text.WriteByte('\n')
	}
	return result, nil
}

func lookupValue(object domain.RequestObject, name string) (any, bool) {
	return object.Lookup(name)
}

func stringValue(value any) string {
	text, _ := value.(string)
	return text
}

// pythonSetMember evaluates `object.get(name) in set`: strings are members or
// not, other scalars never match, and containers raise TypeError in Python.
func pythonSetMember(object domain.RequestObject, name string, set map[string]bool) (string, error) {
	value, _ := lookupValue(object, name)
	switch typed := value.(type) {
	case domain.RequestObject, []any:
		return "", ErrStaffBoardDocumentUnhashable
	case string:
		if set[typed] {
			return typed, nil
		}
	}
	return "", nil
}

func pythonTruthy(value any) bool {
	switch typed := value.(type) {
	case nil:
		return false
	case bool:
		return typed
	case string:
		return typed != ""
	case json.Number:
		if !strings.ContainsAny(string(typed), ".eE") {
			integer, ok := new(big.Int).SetString(string(typed), 10)
			return !ok || integer.Sign() != 0
		}
		number, _ := strconv.ParseFloat(string(typed), 64)
		return number != 0
	case domain.RequestObject:
		return len(typed) > 0
	case []any:
		return len(typed) > 0
	}
	return true
}

// Python str.isspace: Unicode category Zs or bidirectional class WS, B or S.
func pythonIsSpace(r rune) bool {
	switch {
	case r >= 0x09 && r <= 0x0d, r >= 0x1c && r <= 0x20, r == 0x85, r == 0xa0, r == 0x1680,
		r >= 0x2000 && r <= 0x200a, r == 0x2028, r == 0x2029, r == 0x202f, r == 0x205f, r == 0x3000:
		return true
	}
	return false
}

// pythonStrip re-slices the original bytes so preserved surrogate code units
// survive; whitespace is always valid UTF-8, never a surrogate.
func pythonStrip(value string) string {
	start := 0
	for start < len(value) {
		r, size := utf8.DecodeRuneInString(value[start:])
		if !pythonIsSpace(r) || (r == utf8.RuneError && size == 1) {
			break
		}
		start += size
	}
	end := len(value)
	for end > start {
		r, size := utf8.DecodeLastRuneInString(value[start:end])
		if !pythonIsSpace(r) || (r == utf8.RuneError && size == 1) {
			break
		}
		end -= size
	}
	return value[start:end]
}

// pythonSafeURL ports staff_board.safe_url, including Python 3.13 urlsplit's
// ValueError paths. Control characters and spaces are rejected first, so
// urlsplit's leading-space stripping and tab/newline removal never apply.
func pythonSafeURL(value any, image bool) bool {
	text, ok := value.(string)
	if !ok {
		return false
	}
	for _, r := range pythonCodePoints(text) {
		if r <= 0x20 || r == 0x7f {
			return false
		}
	}
	if image && staffBoardDataImage.MatchString(text) {
		return true
	}
	scheme, netloc, ok := pythonURLSplit(text)
	if !ok {
		return false
	}
	return ((scheme == "http" || scheme == "https") && netloc != "") || (!image && (scheme == "mailto" || scheme == "tel"))
}

func pythonURLSplit(url string) (string, string, bool) {
	scheme, netloc := "", ""
	if i := strings.IndexByte(url, ':'); i > 0 && isASCIILetter(url[0]) {
		valid := true
		for j := 0; j < i; j++ {
			c := url[j]
			if !isASCIILetter(c) && !(c >= '0' && c <= '9') && c != '+' && c != '-' && c != '.' {
				valid = false
				break
			}
		}
		if valid {
			scheme, url = strings.ToLower(url[:i]), url[i+1:]
		}
	}
	if strings.HasPrefix(url, "//") {
		rest := url[2:]
		delim := len(rest)
		for _, c := range []byte("/?#") {
			if i := strings.IndexByte(rest, c); i >= 0 && i < delim {
				delim = i
			}
		}
		netloc = rest[:delim]
		open, close := strings.Contains(netloc, "["), strings.Contains(netloc, "]")
		if open != close {
			return "", "", false
		}
		if open && !pythonBracketedNetlocValid(netloc) {
			return "", "", false
		}
	}
	if !pythonNetlocNFKCValid(netloc) {
		return "", "", false
	}
	return scheme, netloc, true
}

func isASCIILetter(c byte) bool { return c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' }

func pythonNetlocNFKCValid(netloc string) bool {
	ascii := true
	for i := 0; i < len(netloc); i++ {
		if netloc[i] >= 0x80 {
			ascii = false
			break
		}
	}
	if netloc == "" || ascii {
		return true
	}
	n := strings.NewReplacer("@", "", ":", "", "#", "", "?", "").Replace(netloc)
	normalized := norm.NFKC.String(n)
	return n == normalized || !strings.ContainsAny(normalized, "/?#@:")
}

func pythonBracketedNetlocValid(netloc string) bool {
	hostAndPort := netloc[strings.LastIndexByte(netloc, '@')+1:]
	var hostname string
	if i := strings.IndexByte(hostAndPort, '['); i >= 0 {
		if i > 0 {
			return false
		}
		bracketed := hostAndPort[i+1:]
		port := ""
		hostname = bracketed
		if j := strings.IndexByte(bracketed, ']'); j >= 0 {
			hostname, port = bracketed[:j], bracketed[j+1:]
		}
		if port != "" && !strings.HasPrefix(port, ":") {
			return false
		}
	} else {
		hostname, _, _ = strings.Cut(hostAndPort, ":")
	}
	if strings.HasPrefix(hostname, "v") || strings.HasPrefix(hostname, "V") {
		return pythonIPvFuture.MatchString(hostname)
	}
	if pythonIPv4(hostname) {
		return false // An IPv4 address cannot be in brackets.
	}
	return pythonIPv6(hostname)
}

// pythonIPv4 ports ipaddress.IPv4Address(str) acceptance.
func pythonIPv4(value string) bool {
	if value == "" || strings.Contains(value, "/") {
		return false
	}
	octets := strings.Split(value, ".")
	if len(octets) != 4 {
		return false
	}
	for _, octet := range octets {
		if octet == "" || len(octet) > 3 || (octet != "0" && octet[0] == '0') {
			return false
		}
		for i := 0; i < len(octet); i++ {
			if octet[i] < '0' || octet[i] > '9' {
				return false
			}
		}
		if number, _ := strconv.Atoi(octet); number > 255 {
			return false
		}
	}
	return true
}

// pythonIPv6 ports ipaddress.IPv6Address(str) acceptance, including scope IDs.
func pythonIPv6(value string) bool {
	if strings.Contains(value, "/") {
		return false
	}
	if address, scope, scoped := strings.Cut(value, "%"); scoped {
		if scope == "" || strings.Contains(scope, "%") {
			return false
		}
		value = address
	}
	if value == "" || len(pythonCodePoints(value)) > 45 {
		return false
	}
	parts := strings.SplitN(value, ":", 10)
	if len(parts) < 3 {
		return false
	}
	if last := parts[len(parts)-1]; strings.Contains(last, ".") {
		if !pythonIPv4(last) {
			return false
		}
		parts = append(parts[:len(parts)-1], "0", "0")
	}
	if len(parts) > 9 {
		return false
	}
	skip := -1
	for i := 1; i < len(parts)-1; i++ {
		if parts[i] == "" {
			if skip >= 0 {
				return false
			}
			skip = i
		}
	}
	var high, low int
	if skip >= 0 {
		high, low = skip, len(parts)-skip-1
		if parts[0] == "" {
			if high--; high != 0 {
				return false
			}
		}
		if parts[len(parts)-1] == "" {
			if low--; low != 0 {
				return false
			}
		}
		if 8-(high+low) < 1 {
			return false
		}
	} else {
		if len(parts) != 8 || parts[0] == "" || parts[len(parts)-1] == "" {
			return false
		}
		high = len(parts)
	}
	for _, part := range append(append([]string{}, parts[:high]...), parts[len(parts)-low:]...) {
		if part == "" || len(part) > 4 {
			return false
		}
		for i := 0; i < len(part); i++ {
			c := part[i]
			if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f' || c >= 'A' && c <= 'F') {
				return false
			}
		}
	}
	return true
}
