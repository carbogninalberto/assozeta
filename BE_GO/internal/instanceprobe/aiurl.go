package instanceprobe

import (
	"net"
	"regexp"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/idna2008"
)

// AIRequest is the request requests 2.34.2 / urllib3 2.8.0 sends for an AI
// base URL: urllib3's parse_url (host normalisation, dot-segment removal,
// percent encoding) followed by PreparedRequest.prepare_url (the scheme and
// host lower-cased, requote_uri) and urllib3's _encode_target (the request
// line path). Port is the port actually connected to (the scheme default
// when the URL has none or the port is 0).
type AIRequest struct {
	// URL is PreparedRequest.url (what the golden lists as "url").
	URL string
	// Scheme, ConnectHost, Port, HostHeader and Target are the golden's
	// "scheme", "connect_host", "port", "host_header" and "target".
	Scheme      string
	ConnectHost string
	Port        int
	HostHeader  string
	Target      string
}

type aiRequestOutcome int

const (
	aiRequestReady aiRequestOutcome = iota
	aiRequestInvalid
	aiRequestRaises
)

// prepareAIRequest follows integration_probe.verify_integration's url split
// check, then requests.get(base_url.rstrip('/') + '/models'). It returns
// aiRequestInvalid for the probe's "URL del provider non valido." answer,
// aiRequestRaises for an exception before the request is sent (the generic
// failure), and aiRequestReady with the request bytes. The only input that
// stays unported is an empty user information pair, which the settings
// endpoint cannot store.
func prepareAIRequest(base string) (*AIRequest, aiRequestOutcome, error) {
	split, err := pyURLSplit(base)
	if err != nil {
		return nil, aiRequestRaises, nil
	}
	if split.scheme != "http" && split.scheme != "https" || split.hostname == "" ||
		split.username != "" || split.password != "" || split.query != "" || split.fragment != "" {
		return nil, aiRequestInvalid, nil
	}
	if split.hasUserInfo && split.username == "" && split.password == "" {
		// urlsplit accepts 'https://:@host' and requests rewrites it, but the
		// settings endpoint cannot store it: kept refused.
		return nil, aiRequestReady, ErrProbeNotPorted
	}

	raw := strings.TrimRight(base, "/") + "/models"
	parsed, err := urllib3ParseURL(raw)
	if err != nil {
		return nil, aiRequestRaises, nil
	}
	// PreparedRequest.prepare_url: requests only prepares http(s) URLs.
	if strings.Contains(raw, ":") && !strings.HasPrefix(strings.ToLower(raw), "http") {
		return nil, aiRequestRaises, nil
	}
	if parsed.scheme == "" || parsed.host == "" {
		return nil, aiRequestRaises, nil
	}
	if !isASCII(parsed.host) {
		return nil, aiRequestRaises, nil // _get_idna_encoded_host, dead after urllib3
	}
	if strings.HasPrefix(parsed.host, "*") || strings.HasPrefix(parsed.host, ".") {
		return nil, aiRequestRaises, nil
	}

	netloc := ""
	if parsed.auth != nil {
		netloc = *parsed.auth + "@"
	}
	netloc += parsed.host
	if parsed.port != nil && *parsed.port != 0 {
		netloc += ":" + strconv.Itoa(*parsed.port)
	}
	path := parsed.path
	if path == "" {
		path = "/"
	}
	assembled := parsed.scheme + "://" + netloc + path
	if parsed.query != "" {
		assembled += "?" + parsed.query
	}
	if parsed.fragment != "" {
		assembled += "#" + parsed.fragment
	}
	preparedURL := requoteURI(assembled)

	final, err := urllib3ParseURL(preparedURL)
	if err != nil {
		return nil, aiRequestRaises, nil
	}
	pathURL, err := pyURLSplit(preparedURL)
	if err != nil {
		return nil, aiRequestRaises, nil
	}
	requestPath := pathURL.path
	if requestPath == "" {
		requestPath = "/"
	}
	target := encodeInvalidChars(requestPath, pathChars)
	if pathURL.query != "" {
		target += "?" + encodeInvalidChars(pathURL.query, queryChars)
	}

	defaultPort := 443
	if final.scheme == "http" {
		defaultPort = 80
	}
	connectPort := defaultPort
	if final.port != nil && *final.port != 0 {
		connectPort = *final.port
	}
	hostHeader := final.host
	if final.port != nil && *final.port != 0 && *final.port != defaultPort {
		hostHeader += ":" + strconv.Itoa(*final.port)
	}
	return &AIRequest{
		URL:         preparedURL,
		Scheme:      final.scheme,
		ConnectHost: strings.Trim(final.host, "[]"),
		Port:        connectPort,
		HostHeader:  hostHeader,
		Target:      target,
	}, aiRequestReady, nil
}

// integrationRequest is IntegrationTarget's work, also returning the request
// the probe must send. A nil request with invalid false is an input for which
// requests raises before sending (the generic failure).
func integrationRequest(provider string, value map[string]any) (*AIRequest, bool, error) {
	if provider != "ai" || !Truthy(value["api_key"]) {
		return nil, false, nil
	}
	base := value["base_url"]
	if !Truthy(base) {
		base = "https://api.openai.com/v1"
	}
	raw, ok := base.(string)
	if !ok {
		return nil, false, nil // urlsplit of a non-string raises
	}
	request, outcome, err := prepareAIRequest(raw)
	if err != nil {
		return nil, false, err
	}
	switch outcome {
	case aiRequestInvalid:
		return nil, true, nil
	case aiRequestRaises:
		return nil, false, nil
	}
	return request, false, nil
}

// ---------------------------------------------------------------------------
// urllib.parse.urlsplit (the probe's own check)
// ---------------------------------------------------------------------------

type pySplit struct {
	scheme      string
	hostname    string
	username    string
	password    string
	hasUserInfo bool
	path, query string
	fragment    string
}

var pySchemeChars = func() map[byte]bool {
	set := map[byte]bool{}
	for _, c := range []byte("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+-.") {
		set[c] = true
	}
	return set
}()

// pyURLSplit is urllib.parse.urlsplit for a str, including its bracket check.
func pyURLSplit(raw string) (*pySplit, error) {
	url := strings.TrimLeftFunc(raw, func(r rune) bool { return r <= 0x20 })
	url = strings.NewReplacer("\t", "", "\r", "", "\n", "").Replace(url)

	split := &pySplit{}
	if i := strings.IndexByte(url, ':'); i > 0 && url[0] < 0x80 && isASCIIAlpha(url[0]) {
		valid := true
		for j := 0; j < i; j++ {
			if !pySchemeChars[url[j]] {
				valid = false
				break
			}
		}
		if valid {
			split.scheme = strings.ToLower(url[:i])
			url = url[i+1:]
		}
	}
	if strings.HasPrefix(url, "//") {
		rest := url[2:]
		end := len(rest)
		for i := 0; i < len(rest); i++ {
			if rest[i] == '/' || rest[i] == '?' || rest[i] == '#' {
				end = i
				break
			}
		}
		netloc, tail := rest[:end], rest[end:]
		if strings.Contains(netloc, "[") != strings.Contains(netloc, "]") {
			return nil, strconv.ErrSyntax // "Invalid IPv6 URL"
		}
		if strings.Contains(netloc, "[") && strings.Contains(netloc, "]") {
			if err := checkBracketedNetloc(netloc); err != nil {
				return nil, err
			}
		}
		url = tail
		hostInfo := netloc
		if i := strings.LastIndexByte(netloc, '@'); i >= 0 {
			split.hasUserInfo = true
			userInfo, host := netloc[:i], netloc[i+1:]
			hostInfo = host
			if j := strings.IndexByte(userInfo, ':'); j >= 0 {
				split.username, split.password = userInfo[:j], userInfo[j+1:]
			} else {
				split.username = userInfo
			}
		}
		hostname := hostInfo
		if i := strings.IndexByte(hostInfo, '['); i >= 0 {
			if j := strings.IndexByte(hostInfo[i+1:], ']'); j >= 0 {
				hostname = hostInfo[i+1 : i+1+j]
			}
		} else if i := strings.IndexByte(hostInfo, ':'); i >= 0 {
			hostname = hostInfo[:i]
		}
		split.hostname = pyHostnameLower(hostname)
	}
	if i := strings.IndexByte(url, '#'); i >= 0 {
		url, split.fragment = url[:i], url[i+1:]
	}
	if i := strings.IndexByte(url, '?'); i >= 0 {
		url, split.query = url[:i], url[i+1:]
	}
	split.path = url
	return split, nil
}

// pyHostnameLower is SplitResult.hostname: the address is lower-cased, a zone
// identifier after '%' is kept as typed.
func pyHostnameLower(hostname string) string {
	if hostname == "" {
		return ""
	}
	if i := strings.IndexByte(hostname, '%'); i >= 0 {
		return dto.PythonLower(hostname[:i]) + hostname[i:]
	}
	return dto.PythonLower(hostname)
}

// checkBracketedNetloc mirrors urllib.parse._check_bracketed_netloc.
func checkBracketedNetloc(netloc string) error {
	hostPort := netloc
	if i := strings.LastIndexByte(netloc, '@'); i >= 0 {
		hostPort = netloc[i+1:]
	}
	open := strings.IndexByte(hostPort, '[')
	if open < 0 {
		return nil
	}
	if open != 0 {
		return strconv.ErrSyntax // "Invalid IPv6 URL"
	}
	close := strings.IndexByte(hostPort[open+1:], ']')
	if close < 0 {
		return strconv.ErrSyntax
	}
	hostname := hostPort[open+1 : open+1+close]
	after := hostPort[open+1+close+1:]
	if after != "" && !strings.HasPrefix(after, ":") {
		return strconv.ErrSyntax
	}
	if strings.HasPrefix(hostname, "v") || strings.HasPrefix(hostname, "V") {
		if !ipvFutureRE.MatchString(hostname) {
			return strconv.ErrSyntax
		}
		return nil
	}
	address := hostname
	if i := strings.IndexByte(address, '%'); i >= 0 {
		address = address[:i]
	}
	ip := net.ParseIP(address)
	if ip == nil || !strings.Contains(address, ":") {
		return strconv.ErrSyntax
	}
	return nil
}

var ipvFutureRE = regexp.MustCompile(`^[vV][0-9A-Fa-f]+\..+$`)

// ---------------------------------------------------------------------------
// urllib3.util.url.parse_url
// ---------------------------------------------------------------------------

type parsedURL struct {
	scheme   string
	auth     *string
	host     string
	port     *int
	path     string
	query    string
	fragment string
}

var (
	urllib3URIRe    = regexp.MustCompile(`(?s)^(?:([a-zA-Z][a-zA-Z0-9+.-]*):)?(?://([^\\/?#]*))?([^?#]*)(?:\?([^#]*))?(?:#(.*))?$`)
	urllib3SchemeRe = regexp.MustCompile(`^(?:[a-zA-Z][a-zA-Z0-9+-]*:|/)`)
	hostRegNameRE   = regexp.MustCompile(`^(?:[^\[\]%:/?#]|%[0-9A-Fa-f]{2})*$`)
	ipv4LikeRE      = regexp.MustCompile(`^(?:0[xX][0-9A-Fa-f]+|[0-9]+)(?:\.(?:0[xX][0-9A-Fa-f]+|[0-9]+)){0,3}$`)
	zoneIDRE        = regexp.MustCompile(`^(?:[A-Za-z0-9._~-]|%[0-9A-Fa-f]{2})+$`)
	percentRE       = regexp.MustCompile(`%[A-Fa-f0-9]{2}`)
	hostPercentRE   = regexp.MustCompile(`%[A-Fa-f0-9]{2}|%`)
)

var errLocationParse = strconv.ErrSyntax

func urllib3ParseURL(raw string) (*parsedURL, error) {
	url := raw
	if !urllib3SchemeRe.MatchString(url) {
		url = "//" + url
	}
	loc := urllib3URIRe.FindStringSubmatchIndex(url)
	if loc == nil {
		return nil, errLocationParse
	}
	group := func(index int) (string, bool) {
		if loc[2*index] < 0 {
			return "", false
		}
		return url[loc[2*index]:loc[2*index+1]], true
	}
	scheme, hasScheme := group(1)
	authority, hasAuthority := group(2)
	path, _ := group(3)
	query, hasQuery := group(4)
	fragment, hasFragment := group(5)

	result := &parsedURL{}
	if hasScheme {
		result.scheme = strings.ToLower(scheme)
	}
	normalizeURI := !hasScheme || result.scheme == "http" || result.scheme == "https"

	if hasAuthority && authority != "" {
		authText, hostPort := "", authority
		if i := strings.LastIndexByte(authority, '@'); i >= 0 {
			authText, hostPort = authority[:i], authority[i+1:]
		}
		host, port, err := matchHostPort(hostPort)
		if err != nil {
			return nil, err
		}
		if authText != "" {
			encoded := encodeInvalidChars(authText, userinfoChars)
			result.auth = &encoded
		}
		if port != nil {
			if *port < 0 || *port > 65535 {
				return nil, errLocationParse
			}
		}
		normalized, err := normalizeHost(host, result.scheme)
		if err != nil {
			return nil, err
		}
		result.host, result.port = normalized, port
	}

	if normalizeURI && path != "" {
		path = encodeInvalidChars(removePathDotSegments(path), pathChars)
	}
	if normalizeURI && query != "" {
		query = encodeInvalidChars(query, queryChars)
	}
	if normalizeURI && fragment != "" {
		fragment = encodeInvalidChars(fragment, fragmentChars)
	}
	result.path = path
	if hasQuery {
		result.query = query
	}
	if hasFragment {
		result.fragment = fragment
	}
	return result, nil
}

// matchHostPort is urllib3's _HOST_PORT_RE.fullmatch, returning the port text
// normalised (leading zeros removed) as an int.
func matchHostPort(hostPort string) (string, *int, error) {
	var host, rest string
	if strings.HasPrefix(hostPort, "[") {
		close := strings.IndexByte(hostPort, ']')
		if close < 0 {
			return "", nil, errLocationParse
		}
		host, rest = hostPort[:close+1], hostPort[close+1:]
		if err := matchIPv6Host(host); err != nil {
			return "", nil, err
		}
	} else {
		host, rest = hostPort, ""
		if i := strings.IndexByte(hostPort, ':'); i >= 0 {
			host, rest = hostPort[:i], hostPort[i:]
		}
		if !hostRegNameRE.MatchString(host) {
			return "", nil, errLocationParse
		}
	}
	if rest == "" {
		return host, nil, nil
	}
	if !strings.HasPrefix(rest, ":") {
		return "", nil, errLocationParse
	}
	text := rest[1:]
	if text == "" {
		return host, nil, nil
	}
	for i := 0; i < len(text); i++ {
		if text[i] < '0' || text[i] > '9' {
			return "", nil, errLocationParse
		}
	}
	trimmed := strings.TrimLeft(text, "0")
	if trimmed == "" {
		trimmed = "0"
	}
	if len(trimmed) > 5 {
		return "", nil, errLocationParse
	}
	value, err := strconv.Atoi(trimmed)
	if err != nil || value < 0 || value > 65535 {
		return "", nil, errLocationParse
	}
	return host, &value, nil
}

// matchIPv6Host validates urllib3's _IPV6_ADDRZ_PAT for a bracketed host.
func matchIPv6Host(host string) error {
	if len(host) < 2 || host[0] != '[' || host[len(host)-1] != ']' {
		return errLocationParse
	}
	inside := host[1 : len(host)-1]
	address := inside
	if i := strings.IndexByte(inside, '%'); i >= 0 {
		address = inside[:i]
		zone := inside[i+1:]
		if strings.HasPrefix(zone, "25") && len(zone) > 2 {
			zone = zone[2:]
		}
		if !zoneIDRE.MatchString(zone) {
			return errLocationParse
		}
	}
	ip := net.ParseIP(address)
	if ip == nil || !strings.Contains(address, ":") {
		return errLocationParse
	}
	return nil
}

// ---------------------------------------------------------------------------
// urllib3.util.url._normalize_host
// ---------------------------------------------------------------------------

func normalizeHost(host, scheme string) (string, error) {
	if host == "" {
		return host, nil
	}
	for i := 0; i < len(host); i++ {
		if host[i] <= 0x20 || host[i] == 0x7f {
			return "", errLocationParse
		}
	}
	if scheme != "http" && scheme != "https" {
		return host, nil
	}
	if strings.HasPrefix(host, "[") {
		if i := strings.IndexByte(host, '%'); i >= 0 {
			zone := host[i : len(host)-1]
			if strings.HasPrefix(zone, "%25") && zone != "%25" {
				zone = zone[3:]
			} else {
				zone = zone[1:]
			}
			var err error
			if zone, err = normalizeZonePercent(zone); err != nil {
				return "", err
			}
			zone = encodeInvalidChars(zone, unreservedChars)
			return dto.PythonLower(host[:i]) + "%" + zone + "]", nil
		}
		return dto.PythonLower(host), nil
	}
	if ipv4LikeRE.MatchString(host) {
		return host, nil
	}
	if strings.Contains(host, "%") {
		normalized, err := normalizeHostPercent(host)
		if err != nil {
			return "", err
		}
		host = normalized
	}
	labels := strings.Split(host, ".")
	for i, label := range labels {
		encoded, err := idnaEncode(label)
		if err != nil {
			return "", err
		}
		labels[i] = encoded
	}
	return strings.Join(labels, "."), nil
}

func normalizeHostPercent(host string) (string, error) {
	var out strings.Builder
	for i := 0; i < len(host); {
		if host[i] != '%' {
			out.WriteByte(host[i])
			i++
			continue
		}
		if i+2 >= len(host) || !isHex(host[i+1]) || !isHex(host[i+2]) {
			return "", errLocationParse
		}
		value := hexValue(host[i+1])<<4 | hexValue(host[i+2])
		if value < 0x20 || value == 0x7f {
			return "", errLocationParse
		}
		if unreservedChars(value) {
			out.WriteByte(value)
		} else {
			out.WriteByte('%')
			out.WriteByte(upperHex(host[i+1]))
			out.WriteByte(upperHex(host[i+2]))
		}
		i += 3
	}
	return out.String(), nil
}

func normalizeZonePercent(zone string) (string, error) {
	var out strings.Builder
	for i := 0; i < len(zone); {
		if zone[i] != '%' {
			out.WriteByte(zone[i])
			i++
			continue
		}
		if i+2 >= len(zone) || !isHex(zone[i+1]) || !isHex(zone[i+2]) {
			return "", errLocationParse
		}
		value := hexValue(zone[i+1])<<4 | hexValue(zone[i+2])
		if value < 0x20 || value == 0x7f {
			return "", errLocationParse
		}
		out.WriteByte('%')
		out.WriteByte(upperHex(zone[i+1]))
		out.WriteByte(upperHex(zone[i+2]))
		i += 3
	}
	return out.String(), nil
}

// idnaEncode is urllib3's _idna_encode: an ASCII label is lower-cased (its
// percent escapes upper-cased), any other label goes through the idna
// package's strict, std3 IDNA 2008 encoder after str.lower().
func idnaEncode(label string) (string, error) {
	if !isASCII(label) {
		encoded, err := idna2008.Encode(dto.PythonLower(label))
		if err != nil {
			return "", errLocationParse
		}
		return encoded, nil
	}
	return percentRE.ReplaceAllStringFunc(dto.PythonLower(label), strings.ToUpper), nil
}

// ---------------------------------------------------------------------------
// urllib3.util.url._remove_path_dot_segments / _encode_invalid_chars
// ---------------------------------------------------------------------------

func removePathDotSegments(path string) string {
	segments := strings.Split(path, "/")
	output := make([]string, 0, len(segments))
	for _, segment := range segments {
		if segment == "." {
			continue
		}
		if segment != ".." {
			output = append(output, segment)
		} else if len(output) > 0 {
			output = output[:len(output)-1]
		}
	}
	if strings.HasPrefix(path, "/") && (len(output) == 0 || output[0] != "") {
		output = append([]string{""}, output...)
	}
	if strings.HasSuffix(path, "/.") || strings.HasSuffix(path, "/..") {
		output = append(output, "")
	}
	return strings.Join(output, "/")
}

func unreservedChars(c byte) bool {
	return c >= 'A' && c <= 'Z' || c >= 'a' && c <= 'z' || c >= '0' && c <= '9' ||
		c == '.' || c == '_' || c == '-' || c == '~'
}

func subDelimChars(c byte) bool {
	return strings.IndexByte("!$&'()*+,;=", c) >= 0
}

func pathChars(c byte) bool {
	return unreservedChars(c) || subDelimChars(c) || c == ':' || c == '@' || c == '/'
}

func queryChars(c byte) bool {
	return pathChars(c) || c == '?'
}

func userinfoChars(c byte) bool {
	return unreservedChars(c) || subDelimChars(c) || c == ':'
}

func fragmentChars(c byte) bool { return queryChars(c) }

// encodeInvalidChars is urllib3's _encode_invalid_chars for a str.
func encodeInvalidChars(component string, allowed func(byte) bool) string {
	if !strings.Contains(component, "%") && isASCII(component) && onlyAllowed(component, allowed) {
		return component
	}
	normalized := percentRE.ReplaceAllStringFunc(component, strings.ToUpper)
	percentEncodings := len(percentRE.FindAllString(normalized, -1))
	percentEncoded := percentEncodings == strings.Count(normalized, "%")
	var out strings.Builder
	for i := 0; i < len(normalized); i++ {
		b := normalized[i]
		if percentEncoded && b == '%' || b < 0x80 && allowed(b) {
			out.WriteByte(b)
		} else {
			out.WriteByte('%')
			out.WriteByte(upperHexDigits[b>>4])
			out.WriteByte(upperHexDigits[b&0x0f])
		}
	}
	return out.String()
}

func onlyAllowed(s string, allowed func(byte) bool) bool {
	for i := 0; i < len(s); i++ {
		if !allowed(s[i]) {
			return false
		}
	}
	return true
}

// ---------------------------------------------------------------------------
// requests.utils.requote_uri
// ---------------------------------------------------------------------------

const (
	alwaysSafeChars    = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-~"
	safeWithPercent    = "!#$%&'()*+,/:;=?@[]~"
	safeWithoutPercent = "!#$&'()*+,/:;=?@[]~"
)

// requoteURI is requests.utils.requote_uri: unquote unreserved escapes, then
// quote everything outside the URI safe set.
func requoteURI(uri string) string {
	unquoted, ok := unquoteUnreserved(uri)
	if !ok {
		return quoteURI(uri, safeWithoutPercent)
	}
	return quoteURI(unquoted, safeWithPercent)
}

func unquoteUnreserved(uri string) (string, bool) {
	parts := strings.Split(uri, "%")
	for i := 1; i < len(parts); i++ {
		h := parts[i]
		if len(h) < 2 || !isASCIIAlnum(h[0]) || !isASCIIAlnum(h[1]) {
			parts[i] = "%25" + parts[i]
			continue
		}
		if !isHex(h[0]) || !isHex(h[1]) {
			return "", false
		}
		c := hexValue(h[0])<<4 | hexValue(h[1])
		if unreservedChars(c) {
			parts[i] = string([]byte{c}) + h[2:]
		} else {
			parts[i] = "%" + h
		}
	}
	return strings.Join(parts, ""), true
}

func quoteURI(s, safe string) string {
	var out strings.Builder
	for i := 0; i < len(s); i++ {
		b := s[i]
		if b < 0x80 && (isASCIIAlnum(b) || strings.IndexByte(alwaysSafeChars, b) >= 0 || strings.IndexByte(safe, b) >= 0) {
			out.WriteByte(b)
		} else {
			out.WriteByte('%')
			out.WriteByte(upperHexDigits[b>>4])
			out.WriteByte(upperHexDigits[b&0x0f])
		}
	}
	return out.String()
}

// ---------------------------------------------------------------------------
// small helpers
// ---------------------------------------------------------------------------

const upperHexDigits = "0123456789ABCDEF"

func isASCII(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] >= 0x80 {
			return false
		}
	}
	return true
}

func isASCIIAlpha(c byte) bool { return c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' }

func isASCIIAlnum(c byte) bool { return isASCIIAlpha(c) || c >= '0' && c <= '9' }

func isHex(c byte) bool {
	return c >= '0' && c <= '9' || c >= 'a' && c <= 'f' || c >= 'A' && c <= 'F'
}

func hexValue(c byte) byte {
	switch {
	case c >= '0' && c <= '9':
		return c - '0'
	case c >= 'a' && c <= 'f':
		return c - 'a' + 10
	default:
		return c - 'A' + 10
	}
}

func upperHex(c byte) byte {
	if c >= 'a' && c <= 'f' {
		return c - 'a' + 'A'
	}
	return c
}
