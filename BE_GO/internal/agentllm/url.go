package agentllm

// This file ports the URL handling an AsyncOpenAI base URL goes through:
// httpx/_urlparse.py's urlparse() (validation, host, port and path
// normalisation, percent-encoding), the parts of httpx/_urls.py the SDK reads
// (raw_path, netloc, copy_with), openai/_base_client.py's enforced trailing
// slash and its merge of the relative "chat/completions" URL, and httpx's
// Client._merge_url. The result is the request target and Host header httpx
// sends, so no base URL is handed back to Django because httpx would rewrite
// or reject it.

import (
	"errors"
	"fmt"
	"net"
	"regexp"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/idna2008"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// maxURLLength is httpx._urlparse.MAX_URL_LENGTH, counted in code points.
const maxURLLength = 65536

// InvalidURLError is httpx.InvalidURL: Text is str(exception), the message the
// SDK surfaces when it builds a client or a request.
type InvalidURLError struct{ Text string }

func (e *InvalidURLError) Error() string { return e.Text }

func invalidURL(format string, args ...any) error {
	return &InvalidURLError{Text: fmt.Sprintf(format, args...)}
}

// ErrNotPorted refuses the base URLs whose httpx behaviour this port does not
// reproduce: a host starting with "xn--" that idna.decode rejects (httpx's
// URL.host decodes it while every request is built and the SDK reports the
// IDNAError text, which Go does not reproduce), and a port written with more
// digits than an int holds (Python keeps the value).
var ErrNotPorted = refusal.New("agent model endpoint input not ported")

// errBaseURLEncoding is a base URL that is not UTF-8 text. Go cannot reproduce
// what Python does with the lone surrogates httpx would receive, so such an
// input is left to Django.
var errBaseURLEncoding = errors.New("agentllm: base URL is not UTF-8 text")

const (
	// unreservedCharacters is RFC 3986 section 2.3.
	unreservedCharacters = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
	// subDelims is RFC 3986 section 1.2.2.
	subDelims = "!$&'()*+,;="
	// asciiHostExtra is encode_host's WHATWG_SAFE, kept in ASCII host names.
	asciiHostExtra = "\"`{}%\\"
	// hexDigits are the percent-encoding digits, in upper case like PERCENT().
	hexDigits = "0123456789ABCDEF"
)

// safeCharacters is httpx's `"".join([chr(i) for i in range(0x20, 0x7F) if i
// not in excluded])`, so every printable ASCII character but the excluded
// ones; the unreserved characters are added again because every safe set
// keeps them.
func safeCharacters(excluded string) [128]bool {
	var set [128]bool
	for c := 0x20; c < 0x7f; c++ {
		set[c] = !strings.ContainsRune(excluded, rune(c))
	}
	for c := 0; c < 0x80; c++ {
		if strings.ContainsRune(unreservedCharacters, rune(c)) {
			set[c] = true
		}
	}
	return set
}

// safeFrom is httpx's quote(..., safe=safe): the unreserved characters and
// the ones the caller lists.
func safeFrom(safe string) [128]bool {
	var set [128]bool
	for c := 0; c < 0x80; c++ {
		set[c] = strings.ContainsRune(unreservedCharacters, rune(c)) || strings.ContainsRune(safe, rune(c))
	}
	return set
}

var (
	// httpx's FRAG_SAFE, QUERY_SAFE, PATH_SAFE and USERINFO_SAFE.
	fragmentSafe = safeCharacters(" \"<>`")
	querySafe    = safeCharacters(" \"#<>")
	pathSafe     = safeCharacters(" \"#<>?`{}")
	userinfoSafe = safeCharacters(" \"#<>?`{}" + "/;=@[\\]^|")
	// encode_host keeps the sub-delims and WHATWG_SAFE in an ASCII host.
	hostSafe = safeFrom(subDelims + asciiHostExtra)
)

var (
	// URL_REGEX.
	urlRegex = regexp.MustCompile(`(?:(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*)?:)?` +
		`(?://(?P<authority>[^/?#]*))?(?P<path>[^?#]*)` +
		`(?:\?(?P<query>[^#]*))?(?:#(?P<fragment>.*))?`)
	// AUTHORITY_REGEX.
	authorityRegex = regexp.MustCompile(`(?:(?P<userinfo>.*)@)?(?P<host>(\[.*\]|[^:@]*)):?(?P<port>.*)?`)
	// COMPONENT_REGEX for the components urlparse() is called with, anchored
	// because urlparse() fullmatches them.
	componentRegex = map[string]*regexp.Regexp{
		"scheme":    regexp.MustCompile(`^([a-zA-Z][a-zA-Z0-9+.-]*)?$`),
		"authority": regexp.MustCompile(`^[^/?#]*$`),
		"path":      regexp.MustCompile(`^[^?#]*$`),
		"query":     regexp.MustCompile(`^[^#]*$`),
		"fragment":  regexp.MustCompile(`^.*$`),
	}
	// IPv4_STYLE_HOSTNAME and IPv6_STYLE_HOSTNAME.
	ipv4StyleHostname = regexp.MustCompile(`^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$`)
	ipv6StyleHostname = regexp.MustCompile(`^\[.*\]$`)
	// percentEncodedRegex is PERCENT_ENCODED_REGEX.
	percentEncodedRegex = regexp.MustCompile(`%[A-Fa-f0-9]{2}`)
)

// parseResult is httpx's ParseResult: ASCII components after normalisation.
type parseResult struct {
	scheme   string
	userinfo string
	host     string
	port     *int
	path     string
	query    *string
	fragment *string
}

// authority is ParseResult.authority.
func (r *parseResult) authority() string {
	var out strings.Builder
	if r.userinfo != "" {
		out.WriteString(r.userinfo)
		out.WriteByte('@')
	}
	writeHost(&out, r.host)
	if r.port != nil {
		out.WriteByte(':')
		out.WriteString(strconv.Itoa(*r.port))
	}
	return out.String()
}

func writeHost(out *strings.Builder, host string) {
	if strings.Contains(host, ":") {
		out.WriteByte('[')
		out.WriteString(host)
		out.WriteByte(']')
		return
	}
	out.WriteString(host)
}

// netloc is ParseResult.netloc: the request's Host header.
func (r *parseResult) netloc() string {
	var out strings.Builder
	writeHost(&out, r.host)
	if r.port != nil {
		out.WriteByte(':')
		out.WriteString(strconv.Itoa(*r.port))
	}
	return out.String()
}

// rawPath is URL.raw_path: the path (or "/") and, when present, the query.
func (r *parseResult) rawPath() string {
	path := r.path
	if path == "" {
		path = "/"
	}
	if r.query != nil {
		path += "?" + *r.query
	}
	return path
}

// isAbsoluteURL is URL.is_absolute_url: a scheme and a host to connect to.
func (r *parseResult) isAbsoluteURL() bool { return r.scheme != "" && r.host != "" }

// String is str(ParseResult) and so str(URL).
func (r *parseResult) String() string {
	var out strings.Builder
	if r.scheme != "" {
		out.WriteString(r.scheme)
		out.WriteByte(':')
	}
	if authority := r.authority(); authority != "" {
		out.WriteString("//")
		out.WriteString(authority)
	}
	out.WriteString(r.path)
	if r.query != nil {
		out.WriteByte('?')
		out.WriteString(*r.query)
	}
	if r.fragment != nil {
		out.WriteByte('#')
		out.WriteString(*r.fragment)
	}
	return out.String()
}

// components is the keyword form of urlparse(), which ParseResult.copy_with
// uses to re-parse a changed component.
type components struct {
	scheme    string
	authority string
	path      string
	query     *string
	fragment  *string
	// rawPath replaces path and query with a path and optional query.
	rawPath *string
}

// urlParse is httpx's urlparse(url) for a str URL.
func urlParse(raw string) (*parseResult, error) { return urlParseWith(raw, nil) }

// urlParseWith is urlparse(url, **kwargs).
func urlParseWith(raw string, given *components) (*parseResult, error) {
	if !utf8.ValidString(raw) {
		return nil, errBaseURLEncoding
	}
	if utf8.RuneCountInString(raw) > maxURLLength {
		return nil, invalidURL("URL too long")
	}
	if character, position, found := firstNonPrintableASCII(raw); found {
		return nil, invalidURL("Invalid non-printable ASCII character in URL, %s at position %d.",
			dto.PythonStringRepr(character), position)
	}

	var (
		scheme, authority, path string
		query, fragment         *string
	)
	if given != nil {
		// urlparse() pops raw_path before validating the components.
		path, query = given.path, given.query
		if given.rawPath != nil {
			head, tail, found := strings.Cut(*given.rawPath, "?")
			path, query = head, nil
			if found {
				query = &tail
			}
		}
		scheme, authority, fragment = given.scheme, given.authority, given.fragment
		for _, component := range []struct {
			name, value string
			present     bool
		}{
			{"scheme", scheme, true},
			{"authority", authority, true},
			{"path", path, true},
			{"query", valueOrEmpty(query), query != nil},
			{"fragment", valueOrEmpty(fragment), fragment != nil},
		} {
			if !component.present {
				continue
			}
			if err := validateComponent(component.name, component.value); err != nil {
				return nil, err
			}
		}
	} else {
		groups := newRegexGroups(urlRegex, raw)
		scheme, authority, path = groups.text("scheme"), groups.text("authority"), groups.text("path")
		query, fragment = groups.optional("query"), groups.optional("fragment")
	}

	details := newRegexGroups(authorityRegex, authority)
	userinfo, host, port := details.text("userinfo"), details.text("host"), details.optional("port")

	// normalize_port compares the port with the scheme as it was written, so a
	// URL whose scheme is not lower case keeps its default port until the URL
	// is parsed again.
	parsedScheme := strings.ToLower(scheme)
	parsedUserinfo, err := quote(userinfo, userinfoSafe)
	if err != nil {
		return nil, err
	}
	parsedHost, err := encodeHost(host)
	if err != nil {
		return nil, err
	}
	parsedPort, err := normalizePort(port, scheme)
	if err != nil {
		return nil, err
	}

	hasScheme := parsedScheme != ""
	hasAuthority := parsedUserinfo != "" || parsedHost != "" || parsedPort != nil
	if err := validatePath(path, hasScheme, hasAuthority); err != nil {
		return nil, err
	}
	if hasScheme || hasAuthority {
		path = normalizePath(path)
	}
	parsedPath, err := quote(path, pathSafe)
	if err != nil {
		return nil, err
	}
	parsedQuery, err := quoteOptional(query, querySafe)
	if err != nil {
		return nil, err
	}
	parsedFragment, err := quoteOptional(fragment, fragmentSafe)
	if err != nil {
		return nil, err
	}
	return &parseResult{scheme: parsedScheme, userinfo: parsedUserinfo, host: parsedHost, port: parsedPort,
		path: parsedPath, query: parsedQuery, fragment: parsedFragment}, nil
}

// validateComponent is urlparse()'s check of a component passed by keyword.
func validateComponent(name, value string) error {
	if utf8.RuneCountInString(value) > maxURLLength {
		return invalidURL("URL component '%s' too long", name)
	}
	if character, position, found := firstNonPrintableASCII(value); found {
		return invalidURL("Invalid non-printable ASCII character in URL %s component, %s at position %d.",
			name, dto.PythonStringRepr(character), position)
	}
	if !componentRegex[name].MatchString(value) {
		return invalidURL("Invalid URL component '%s'", name)
	}
	return nil
}

// firstNonPrintableASCII finds the first character that is ASCII and not
// `str.isprintable()`, with its position in code points.
func firstNonPrintableASCII(text string) (string, int, bool) {
	position := 0
	for _, character := range text {
		if character < 0x20 || character == 0x7f {
			return string(character), position, true
		}
		position++
	}
	return "", 0, false
}

// encodeHost is encode_host: IPv4 and IPv6 literals are validated, ASCII host
// names are lowercased and quoted, and the others go through idna.encode().
func encodeHost(host string) (string, error) {
	switch {
	case host == "":
		return "", nil
	case ipv4StyleHostname.MatchString(host):
		if !validIPv4(host) {
			return "", invalidURL("Invalid IPv4 address: %s", dto.PythonStringRepr(host))
		}
		return host, nil
	case ipv6StyleHostname.MatchString(host):
		if !validIPv6(host[1 : len(host)-1]) {
			return "", invalidURL("Invalid IPv6 address: %s", dto.PythonStringRepr(host))
		}
		return host[1 : len(host)-1], nil
	case isASCII(host):
		return quote(strings.ToLower(host), hostSafe)
	}
	encoded, err := idna2008.Encode(dto.PythonLower(host))
	if err != nil {
		return "", invalidURL("Invalid IDNA hostname: %s", dto.PythonStringRepr(host))
	}
	return encoded, nil
}

// validIPv4 is ipaddress.IPv4Address for a host that matched
// IPv4_STYLE_HOSTNAME: four decimal octets without leading zeros, each ≤ 255.
func validIPv4(host string) bool {
	octets := strings.Split(host, ".")
	if len(octets) != 4 {
		return false
	}
	for _, octet := range octets {
		if octet == "" || len(octet) > 3 || !asciiDigits(octet) {
			return false
		}
		if octet != "0" && octet[0] == '0' {
			return false
		}
		if number, err := strconv.Atoi(octet); err != nil || number > 255 {
			return false
		}
	}
	return true
}

// validIPv6 is ipaddress.IPv6Address for the text between the brackets: an
// address of at most 45 characters with an optional non-empty scope id.
func validIPv6(text string) bool {
	if text == "" || strings.Contains(text, "/") {
		return false
	}
	address := text
	if position := strings.IndexByte(text, '%'); position >= 0 {
		scope := text[position+1:]
		if scope == "" || strings.Contains(scope, "%") {
			return false
		}
		address = text[:position]
	}
	if len(address) > 45 || !strings.Contains(address, ":") {
		return false
	}
	return net.ParseIP(address) != nil
}

// normalizePort is normalize_port: int(port), with the scheme's default port
// dropped. Note that httpx compares against the scheme as written, so
// "HTTP://host:80/" keeps port 80 until the URL is re-parsed.
func normalizePort(port *string, scheme string) (*int, error) {
	if port == nil || *port == "" {
		return nil, nil
	}
	number, ok, overflow := pythonInt(*port)
	if overflow {
		return nil, ErrNotPorted
	}
	if !ok {
		return nil, invalidURL("Invalid port: %s", dto.PythonStringRepr(*port))
	}
	defaults := map[string]int{"ftp": 21, "http": 80, "https": 443, "ws": 80, "wss": 443}
	if value, found := defaults[scheme]; found && value == number {
		return nil, nil
	}
	return &number, nil
}

// pythonInt is int(text) for a str: surrounding whitespace, an optional sign,
// and decimal digits of any script (str.isdecimal) that may be separated by
// single underscores. overflow is set when the value does not fit an int.
func pythonInt(text string) (number int, ok, overflow bool) {
	runes := []rune(strings.TrimFunc(text, pythonSpace))
	negative := false
	if len(runes) > 0 && (runes[0] == '+' || runes[0] == '-') {
		negative = runes[0] == '-'
		runes = runes[1:]
	}
	if len(runes) == 0 {
		return 0, false, false
	}
	cleaned := make([]byte, 0, len(runes))
	for position, character := range runes {
		if character == '_' {
			if position == 0 || position == len(runes)-1 ||
				decimalDigit(runes[position-1]) < 0 || decimalDigit(runes[position+1]) < 0 {
				return 0, false, false
			}
			continue
		}
		digit := decimalDigit(character)
		if digit < 0 {
			return 0, false, false
		}
		cleaned = append(cleaned, byte('0'+digit))
	}
	value, err := strconv.Atoi(string(cleaned))
	if err != nil {
		return 0, true, true
	}
	if negative {
		value = -value
	}
	return value, true, false
}

// decimalDigit is the value of a Unicode decimal digit (category Nd, which
// Unicode encodes in runs of ten starting at zero), or -1.
func decimalDigit(r rune) int {
	if r >= '0' && r <= '9' {
		return int(r - '0')
	}
	if r < 0x80 || !unicode.IsDigit(r) {
		return -1
	}
	for _, span := range unicode.Nd.R16 {
		if r >= rune(span.Lo) && r <= rune(span.Hi) {
			return int(r-rune(span.Lo)) % 10
		}
	}
	for _, span := range unicode.Nd.R32 {
		if r >= rune(span.Lo) && r <= rune(span.Hi) {
			return int(r-rune(span.Lo)) % 10
		}
	}
	return -1
}

func validatePath(path string, hasScheme, hasAuthority bool) error {
	if hasAuthority && path != "" && !strings.HasPrefix(path, "/") {
		return invalidURL("For absolute URLs, path must be empty or begin with '/'")
	}
	if !hasScheme && !hasAuthority {
		if strings.HasPrefix(path, "//") {
			return invalidURL("Relative URLs cannot have a path starting with '//'")
		}
		if strings.HasPrefix(path, ":") {
			return invalidURL("Relative URLs cannot have a path starting with ':'")
		}
	}
	return nil
}

// normalizePath is normalize_path: it drops "." and ".." segments.
func normalizePath(path string) string {
	if !strings.Contains(path, ".") {
		return path
	}
	segments := strings.Split(path, "/")
	if !contains(segments, ".") && !contains(segments, "..") {
		return path
	}
	output := make([]string, 0, len(segments))
	for _, segment := range segments {
		switch segment {
		case ".":
		case "..":
			if len(output) > 0 && !(len(output) == 1 && output[0] == "") {
				output = output[:len(output)-1]
			}
		default:
			output = append(output, segment)
		}
	}
	return strings.Join(output, "/")
}

func contains(values []string, value string) bool {
	for _, candidate := range values {
		if candidate == value {
			return true
		}
	}
	return false
}

// quote is httpx's quote: existing %XX escapes are kept as written and every
// other character that is not unreserved or safe is percent-encoded.
func quote(text string, safe [128]bool) (string, error) {
	if !utf8.ValidString(text) {
		return "", errBaseURLEncoding
	}
	var out strings.Builder
	out.Grow(len(text))
	start := 0
	for _, match := range percentEncodedRegex.FindAllStringIndex(text, -1) {
		out.WriteString(percentEncode(text[start:match[0]], safe))
		out.WriteString(text[match[0]:match[1]])
		start = match[1]
	}
	out.WriteString(percentEncode(text[start:], safe))
	return out.String(), nil
}

// quoteOptional is quote for a component that can be absent.
func quoteOptional(text *string, safe [128]bool) (*string, error) {
	if text == nil {
		return nil, nil
	}
	quoted, err := quote(*text, safe)
	if err != nil {
		return nil, err
	}
	return &quoted, nil
}

// percentEncode is percent_encoded: one %XX escape per byte of every character
// that is not unreserved or safe.
func percentEncode(text string, safe [128]bool) string {
	var out strings.Builder
	out.Grow(len(text))
	for _, character := range text {
		if character < 0x80 && safe[character] {
			out.WriteRune(character)
			continue
		}
		for _, octet := range []byte(string(character)) {
			out.WriteByte('%')
			out.WriteByte(hexDigits[octet>>4])
			out.WriteByte(hexDigits[octet&0x0f])
		}
	}
	return out.String()
}

// copyWithRawPath is URL.copy_with(raw_path=...): the components are parsed
// again as urlparse("", scheme=..., authority=..., path=..., query=...,
// fragment=..., raw_path=...), so the path is normalised and quoted twice and
// the port is compared with the lowercased scheme this time.
func (r *parseResult) copyWithRawPath(rawPath string) (*parseResult, error) {
	return urlParseWith("", &components{scheme: r.scheme, authority: r.authority(), path: r.path,
		query: r.query, fragment: r.fragment, rawPath: &rawPath})
}

// enforceTrailingSlash is
// {Async,sync}APIClient._enforce_trailing_slash.
func (r *parseResult) enforceTrailingSlash() (*parseResult, error) {
	rawPath := r.rawPath()
	if strings.HasSuffix(rawPath, "/") {
		return r, nil
	}
	return r.copyWithRawPath(rawPath + "/")
}

// mergeRelative is the merge both _prepare_url and httpx's _merge_url use for
// a relative URL: its raw path is appended to the base URL's raw path.
func (r *parseResult) mergeRelative(merge *parseResult) (*parseResult, error) {
	return r.copyWithRawPath(r.rawPath() + strings.TrimLeft(merge.rawPath(), "/"))
}

// endpoint is where a chat/completions request goes: the scheme to dial, the
// raw_path httpx writes on the request line, and the Host header it sends
// (empty when the URL has no host and httpx sends none).
type endpoint struct {
	scheme string
	target string
	host   string
}

// newEndpoint resolves AsyncOpenAI(base_url=raw) and the request URL the SDK
// builds for "chat/completions": httpx.URL(raw), the enforced trailing slash,
// the SDK's merge of the relative URL, and httpx's own merge of the result onto
// the client's base URL, which repeats the base path for a base URL without a
// scheme or a host.
func newEndpoint(raw string) (endpoint, error) {
	base, err := urlParse(raw)
	if err != nil {
		return endpoint{}, err
	}
	base, err = base.enforceTrailingSlash()
	if err != nil {
		return endpoint{}, err
	}
	relative, err := urlParse("chat/completions")
	if err != nil {
		return endpoint{}, err
	}
	prepared, err := base.mergeRelative(relative)
	if err != nil {
		return endpoint{}, err
	}
	if !prepared.isAbsoluteURL() {
		prepared, err = base.mergeRelative(prepared)
		if err != nil {
			return endpoint{}, err
		}
	}
	if strings.HasPrefix(prepared.host, "xn--") && !idna2008.Decodes(prepared.host) {
		return endpoint{}, ErrNotPorted
	}
	result := endpoint{scheme: prepared.scheme, target: prepared.rawPath()}
	if prepared.host != "" {
		result.host = prepared.netloc()
	}
	return result, nil
}

// regexGroups reads named groups of one match, keeping absent groups apart
// from empty ones.
type regexGroups struct {
	expression *regexp.Regexp
	input      string
	indices    []int
}

func newRegexGroups(expression *regexp.Regexp, input string) regexGroups {
	return regexGroups{expression: expression, input: input, indices: expression.FindStringSubmatchIndex(input)}
}

func (g regexGroups) bounds(name string) (int, int, bool) {
	group := g.expression.SubexpIndex(name)
	if group < 0 || g.indices == nil {
		return 0, 0, false
	}
	start, end := g.indices[2*group], g.indices[2*group+1]
	if start < 0 || end < 0 {
		return 0, 0, false
	}
	return start, end, true
}

// text is a group, or "" when it did not participate.
func (g regexGroups) text(name string) string {
	if start, end, found := g.bounds(name); found {
		return g.input[start:end]
	}
	return ""
}

// optional is a group that stays nil when it did not participate.
func (g regexGroups) optional(name string) *string {
	if start, end, found := g.bounds(name); found {
		value := g.input[start:end]
		return &value
	}
	return nil
}

func valueOrEmpty(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}

func isASCIIDigit(character byte) bool { return character >= '0' && character <= '9' }

func asciiDigits(text string) bool {
	for position := 0; position < len(text); position++ {
		if !isASCIIDigit(text[position]) {
			return false
		}
	}
	return true
}

func isASCII(text string) bool {
	for position := 0; position < len(text); position++ {
		if text[position] >= 0x80 {
			return false
		}
	}
	return true
}
