// Package wsproto is the WebSocket server protocol Django's ASGI server speaks:
// uvicorn 0.35's upgrade detection and the websockets 14 legacy protocol
// (opening handshake checks and their HTTP error bodies, permessage-deflate
// negotiation, frame validation, close handshake, keepalive and the 16 MiB
// message limit). It has no application behaviour; handlers decide whether
// to accept, reject before accept or fail an accepted connection.
package wsproto

import (
	"crypto/sha1"
	"encoding/base64"
	"fmt"
	"net/http"
	"regexp"
	"strings"
)

const acceptGUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

// HandshakeError is the HTTP response websockets writes when the opening
// handshake fails before the connection opens.
type HandshakeError struct {
	Status  int
	Headers [][2]string
	Body    string
}

func (e *HandshakeError) Error() string { return e.Body }

// IsUpgrade is uvicorn's httptools decision to hand a request to the
// WebSocket protocol: the last Connection header lists "upgrade" and the last
// Upgrade header, lowercased, is exactly "websocket".
func IsUpgrade(r *http.Request) bool {
	connection, upgrade := r.Header.Values("Connection"), r.Header.Values("Upgrade")
	if len(connection) == 0 || len(upgrade) == 0 {
		return false
	}
	found := false
	for _, token := range strings.Split(connection[len(connection)-1], ",") {
		if strings.ToLower(strings.TrimSpace(token)) == "upgrade" {
			found = true
		}
	}
	return found && strings.ToLower(upgrade[len(upgrade)-1]) == "websocket"
}

// invalidHandshake is websockets' 400 response; causes are appended after "; ".
func invalidHandshake(message string) *HandshakeError {
	return &HandshakeError{Status: http.StatusBadRequest, Body: "Failed to open a WebSocket connection: " + message + ".\n"}
}

func invalidUpgrade(name, value string) *HandshakeError {
	return &HandshakeError{Status: http.StatusUpgradeRequired, Headers: [][2]string{{"Upgrade", "websocket"}},
		Body: "Failed to open a WebSocket connection: " + headerMessage(name, &value) + ".\n\n" +
			"You cannot access a WebSocket server directly with a browser. You need a WebSocket client.\n"}
}

// headerMessage is InvalidHeader.__str__.
func headerMessage(name string, value *string) string {
	switch {
	case value == nil:
		return "missing " + name + " header"
	case *value == "":
		return "empty " + name + " header"
	default:
		return "invalid " + name + " header: " + *value
	}
}

func ptr(value string) *string { return &value }

// CheckRequest runs websockets' read_request method check and check_request
// on the request headers, returning the client key.
func CheckRequest(r *http.Request) (string, *HandshakeError) {
	if r.Method != http.MethodGet {
		return "", invalidHandshake("did not receive a valid HTTP request; unsupported HTTP method; expected GET; got " + r.Method)
	}
	var connection []string
	for _, value := range headerValues(r, "Connection") {
		items, err := parseList(value, "Connection", parseToken)
		if err != nil {
			return "", invalidHandshake(err.Error())
		}
		connection = append(connection, items...)
	}
	upgradeFound := false
	for _, item := range connection {
		if strings.ToLower(item) == "upgrade" {
			upgradeFound = true
		}
	}
	if !upgradeFound {
		return "", invalidUpgrade("Connection", strings.Join(connection, ", "))
	}
	var upgrade []string
	for _, value := range headerValues(r, "Upgrade") {
		items, err := parseList(value, "Upgrade", parseProtocol)
		if err != nil {
			return "", invalidHandshake(err.Error())
		}
		upgrade = append(upgrade, items...)
	}
	if len(upgrade) != 1 || strings.ToLower(upgrade[0]) != "websocket" {
		return "", invalidUpgrade("Upgrade", strings.Join(upgrade, ", "))
	}
	keys := headerValues(r, "Sec-WebSocket-Key")
	switch len(keys) {
	case 0:
		return "", invalidHandshake(headerMessage("Sec-WebSocket-Key", nil) + "; 'sec-websocket-key'")
	case 1:
	default:
		return "", invalidHandshake(headerMessage("Sec-WebSocket-Key", ptr("multiple values")) + "; 'Sec-WebSocket-Key'")
	}
	key := keys[0]
	raw, reason := pythonStrictBase64(key)
	if reason != "" {
		return "", invalidHandshake(headerMessage("Sec-WebSocket-Key", &key) + "; " + reason)
	}
	if len(raw) != 16 {
		return "", invalidHandshake(headerMessage("Sec-WebSocket-Key", &key))
	}
	versions := headerValues(r, "Sec-WebSocket-Version")
	switch len(versions) {
	case 0:
		return "", invalidHandshake(headerMessage("Sec-WebSocket-Version", nil) + "; 'sec-websocket-version'")
	case 1:
	default:
		return "", invalidHandshake(headerMessage("Sec-WebSocket-Version", ptr("multiple values")) + "; 'Sec-WebSocket-Version'")
	}
	if versions[0] != "13" {
		return "", invalidHandshake(headerMessage("Sec-WebSocket-Version", &versions[0]))
	}
	return key, nil
}

// headerValues returns each header line's value, stripped of spaces and tabs
// as websockets' read_headers does.
func headerValues(r *http.Request, name string) []string {
	var result []string
	for _, value := range r.Header.Values(name) {
		result = append(result, strings.Trim(value, " \t"))
	}
	return result
}

// AcceptValue is the Sec-WebSocket-Accept value for a client key.
func AcceptValue(key string) string {
	sum := sha1.Sum([]byte(key + acceptGUID))
	return base64.StdEncoding.EncodeToString(sum[:])
}

// pythonStrictBase64 is binascii.a2b_base64(data, strict_mode=True) as
// base64.b64decode(..., validate=True) calls it; the second result is the
// binascii.Error message.
func pythonStrictBase64(text string) ([]byte, string) {
	data := []byte(text)
	if len(data) > 0 && data[0] == '=' {
		return nil, "Leading padding not allowed"
	}
	var out []byte
	quad, pads, padding := 0, 0, false
	var left byte
	for i, ch := range data {
		if ch == '=' {
			padding = true
			if quad == 0 {
				return nil, "Excess padding not allowed"
			}
			if quad < 2 {
				continue
			}
			// C: quad_pos >= 2 && quad_pos + ++pads >= 4 (pads counts only from there).
			pads++
			if quad+pads >= 4 {
				if i+1 < len(data) {
					return nil, "Excess data after padding"
				}
				return out, ""
			}
			continue
		}
		value := strings.IndexByte("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/", ch)
		if value < 0 {
			return nil, "Only base64 data is allowed"
		}
		if padding {
			return nil, "Discontinuous padding not allowed"
		}
		pads = 0
		v := byte(value)
		switch quad {
		case 0:
			left = v
		case 1:
			out = append(out, left<<2|v>>4)
			left = v & 0x0f
		case 2:
			out = append(out, left<<4|v>>2)
			left = v & 0x03
		case 3:
			out = append(out, left<<6|v)
		}
		quad = (quad + 1) % 4
	}
	if quad == 1 {
		return nil, fmt.Sprintf("Invalid base64-encoded string: number of data characters (%d) cannot be 1 more than a multiple of 4", len(out)/3*4+1)
	}
	if quad != 0 {
		return nil, "Incorrect padding"
	}
	return out, ""
}

// ---------------------------------------------------------------------------
// websockets.headers: RFC 7230 lists, tokens, protocols and extensions
// ---------------------------------------------------------------------------

var (
	tokenPattern    = regexp.MustCompile("^[-!#$%&'*+.^_`|~0-9a-zA-Z]+")
	protocolPattern = regexp.MustCompile("^[-!#$%&'*+.^_`|~0-9a-zA-Z]+(?:/[-!#$%&'*+.^_`|~0-9a-zA-Z]+)?")
	quotedPattern   = regexp.MustCompile(`^"(?:[\x09\x20-\x21\x23-\x5b\x5d-\x7e]|\\[\x09\x20-\x7e\x80-\xff])*"`)
	unquotePattern  = regexp.MustCompile(`\\([\x09\x20-\x7e\x80-\xff])`)
	tokenFull       = regexp.MustCompile("^[-!#$%&'*+.^_`|~0-9a-zA-Z]+$")
)

type formatError struct {
	name, error, header string
	pos                 int
}

func (e *formatError) Error() string {
	return headerMessage(e.name, ptr(fmt.Sprintf("%s at %d in %s", e.error, e.pos, e.header)))
}

// Header positions count Python str characters; websockets decodes header
// bytes as ISO-8859-1, one character per byte.
func skipOWS(header string, pos int) int {
	for pos < len(header) && (header[pos] == ' ' || header[pos] == '\t') {
		pos++
	}
	return pos
}

func peek(header string, pos int) byte {
	if pos >= len(header) {
		return 0
	}
	return header[pos]
}

type itemParser[T any] func(header string, pos int, name string) (T, int, error)

func parseToken(header string, pos int, name string) (string, int, error) {
	match := tokenPattern.FindString(header[pos:])
	if match == "" {
		return "", pos, &formatError{name, "expected token", header, pos}
	}
	return match, pos + len(match), nil
}

func parseProtocol(header string, pos int, name string) (string, int, error) {
	match := protocolPattern.FindString(header[pos:])
	if match == "" {
		return "", pos, &formatError{name, "expected protocol", header, pos}
	}
	return match, pos + len(match), nil
}

func parseList[T any](header, name string, item itemParser[T]) ([]T, error) {
	pos := 0
	for peek(header, pos) == ',' {
		pos = skipOWS(header, pos+1)
	}
	var items []T
	for {
		value, next, err := item(header, pos, name)
		if err != nil {
			return nil, err
		}
		items = append(items, value)
		pos = skipOWS(header, next)
		if pos == len(header) {
			break
		}
		if peek(header, pos) != ',' {
			return nil, &formatError{name, "expected comma", header, pos}
		}
		pos = skipOWS(header, pos+1)
		for peek(header, pos) == ',' {
			pos = skipOWS(header, pos+1)
		}
		if pos == len(header) {
			break
		}
	}
	return items, nil
}

type extensionParam struct {
	name  string
	value *string
}

type extensionItem struct {
	name   string
	params []extensionParam
}

func parseExtensionParam(header string, pos int, name string) (extensionParam, int, error) {
	key, pos, err := parseToken(header, pos, name)
	if err != nil {
		return extensionParam{}, pos, err
	}
	pos = skipOWS(header, pos)
	param := extensionParam{name: key}
	if peek(header, pos) == '=' {
		pos = skipOWS(header, pos+1)
		var value string
		if peek(header, pos) == '"' {
			before := pos
			match := quotedPattern.FindString(header[pos:])
			if match == "" {
				return param, pos, &formatError{name, "expected quoted string", header, pos}
			}
			value = unquotePattern.ReplaceAllString(match[1:len(match)-1], "$1")
			pos += len(match)
			if !tokenFull.MatchString(value) {
				return param, pos, &formatError{name, "invalid quoted header content", header, before}
			}
		} else {
			value, pos, err = parseToken(header, pos, name)
			if err != nil {
				return param, pos, err
			}
		}
		param.value = &value
		pos = skipOWS(header, pos)
	}
	return param, pos, nil
}

func parseExtensionItem(header string, pos int, name string) (extensionItem, int, error) {
	key, pos, err := parseToken(header, pos, name)
	if err != nil {
		return extensionItem{}, pos, err
	}
	pos = skipOWS(header, pos)
	item := extensionItem{name: key}
	for peek(header, pos) == ';' {
		pos = skipOWS(header, pos+1)
		var param extensionParam
		param, pos, err = parseExtensionParam(header, pos, name)
		if err != nil {
			return item, pos, err
		}
		item.params = append(item.params, param)
	}
	return item, pos, nil
}

// Deflate is the negotiated permessage-deflate configuration.
type Deflate struct {
	ClientNoContextTakeover bool
	ClientMaxWindowBits     int
}

// NegotiateExtensions is process_extensions with uvicorn's single
// ServerPerMessageDeflateFactory() (no configured parameters). It returns
// the response header ("" when nothing is accepted).
func NegotiateExtensions(r *http.Request) (string, *Deflate, *HandshakeError) {
	var offers []extensionItem
	for _, value := range headerValues(r, "Sec-WebSocket-Extensions") {
		items, err := parseList(value, "Sec-WebSocket-Extensions", parseExtensionItem)
		if err != nil {
			return "", nil, invalidHandshake(err.Error())
		}
		offers = append(offers, items...)
	}
	for _, offer := range offers {
		if offer.name != "permessage-deflate" {
			continue
		}
		response, deflate, ok := deflateParameters(offer.params)
		if ok {
			return response, deflate, nil
		}
	}
	return "", nil, nil
}

func windowBits(value *string) (int, bool) {
	if value == nil {
		return 0, false
	}
	for bits := 8; bits <= 15; bits++ {
		if *value == fmt.Sprint(bits) {
			return bits, true
		}
	}
	return 0, false
}

// deflateParameters is _extract_parameters(is_server=True) followed by the
// factory's response rules when nothing is configured.
func deflateParameters(params []extensionParam) (string, *Deflate, bool) {
	var serverNoTakeover, clientNoTakeover bool
	var serverBits, clientBits int
	var clientBitsGiven, clientBitsBare, serverBitsGiven bool
	for _, param := range params {
		switch param.name {
		case "server_no_context_takeover":
			if serverNoTakeover || param.value != nil {
				return "", nil, false
			}
			serverNoTakeover = true
		case "client_no_context_takeover":
			if clientNoTakeover || param.value != nil {
				return "", nil, false
			}
			clientNoTakeover = true
		case "server_max_window_bits":
			bits, ok := windowBits(param.value)
			if serverBitsGiven || !ok {
				return "", nil, false
			}
			serverBits, serverBitsGiven = bits, true
		case "client_max_window_bits":
			if clientBitsGiven {
				return "", nil, false
			}
			clientBitsGiven = true
			if param.value == nil {
				clientBitsBare = true
				continue
			}
			bits, ok := windowBits(param.value)
			if !ok {
				return "", nil, false
			}
			clientBits = bits
		default:
			return "", nil, false
		}
	}
	response := "permessage-deflate"
	if serverNoTakeover {
		response += "; server_no_context_takeover"
	}
	if clientNoTakeover {
		response += "; client_no_context_takeover"
	}
	if serverBitsGiven {
		response += fmt.Sprintf("; server_max_window_bits=%d", serverBits)
	}
	if clientBitsGiven && !clientBitsBare {
		response += fmt.Sprintf("; client_max_window_bits=%d", clientBits)
	}
	return response, &Deflate{ClientNoContextTakeover: clientNoTakeover, ClientMaxWindowBits: clientBits}, true
}
