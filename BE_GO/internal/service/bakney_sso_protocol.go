package service

import (
	"bytes"
	"compress/flate"
	"compress/gzip"
	"compress/zlib"
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"math/big"
	"net"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handoff"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// The v1 wire contract of BE/instance/sso/protocol.py.
const (
	BakneySSOVersion  = "bakney-pairing-v1"
	BakneySSOPrefix   = "/bakney/v1"
	BakneySSOCallback = BakneySSOPrefix + "/callback"
	bakneyCookie      = "__Host-assozeta-sso"
	bakneyDevCookie   = "assozeta-sso-dev"
	bakneyCipherLabel = "assozeta:bakney-sso:v1:"
	// requests.utils.default_user_agent() of the pinned requests release.
	bakneyUserAgent = "python-requests/2.34.2"
)

// BakneySSOError is protocol.SSOError: a code answered as {'error': code}.
type BakneySSOError struct {
	Code   string
	Status int
}

func (e *BakneySSOError) Error() string { return e.Code }

func ssoError(code string, status int) error { return &BakneySSOError{Code: code, Status: status} }

// ErrBakneySSOUnsupported marks inputs whose Python behaviour is not ported
// (non-ASCII hosts needing IDNA, secrets that are not base64); the request is
// answered with the server error page before any write.
var ErrBakneySSOUnsupported = refusal.New("bakney sso input not ported")

var (
	bakneyOpaque   = regexp.MustCompile(`^[A-Za-z0-9_-]{32,128}$`)
	bakneyUUID     = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
	bakneyPath     = regexp.MustCompile(`^(?:/[A-Za-z0-9_-]+)*$`)
	bakneyDNSHost  = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$`)
	bakneyIPFuture = regexp.MustCompile(`^v[a-fA-F0-9]+\..+$`)
)

// BakneySSOSettings are the core.settings values the protocol reads.
type BakneySSOSettings struct {
	Debug          bool
	DeploymentMode string
	AppURL         string
	APIBase        string
	UIOrigin       string
	SecretKeys     []string // SECRET_KEY, then SECRET_KEY_FALLBACKS
}

func (s BakneySSOSettings) developmentHTTP() bool {
	return s.Debug && s.DeploymentMode == "development"
}

// pythonIsSpace is str.isspace for one code point.
func pythonIsSpace(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }

type pythonSplit struct {
	scheme, netloc, path, query, fragment string
}

// pythonURLSplit is urllib.parse.urlsplit (Python 3.13) for ASCII input.
func pythonURLSplit(url string) (pythonSplit, error) {
	url = strings.TrimLeftFunc(url, func(r rune) bool { return r <= ' ' })
	url = strings.NewReplacer("\t", "", "\r", "", "\n", "").Replace(url)
	var result pythonSplit
	if i := strings.IndexByte(url, ':'); i > 0 && url[0] < utf8.RuneSelf && (url[0]|0x20 >= 'a' && url[0]|0x20 <= 'z') {
		valid := true
		for _, c := range url[:i] {
			if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '+' || c == '-' || c == '.') {
				valid = false
				break
			}
		}
		if valid {
			result.scheme, url = strings.ToLower(url[:i]), url[i+1:]
		}
	}
	if strings.HasPrefix(url, "//") {
		end := len(url)
		for _, delimiter := range "/?#" {
			if at := strings.IndexRune(url[2:], delimiter); at >= 0 && at+2 < end {
				end = at + 2
			}
		}
		result.netloc, url = url[2:end], url[end:]
		open, closed := strings.Contains(result.netloc, "["), strings.Contains(result.netloc, "]")
		if open != closed {
			return result, errors.New("Invalid IPv6 URL")
		}
		if open {
			hostinfo := result.netloc[strings.LastIndex(result.netloc, "@")+1:]
			before, bracketed, found := strings.Cut(hostinfo, "[")
			if found {
				if before != "" {
					return result, errors.New("Invalid IPv6 URL")
				}
				host, port, _ := strings.Cut(bracketed, "]")
				if port != "" && !strings.HasPrefix(port, ":") {
					return result, errors.New("Invalid IPv6 URL")
				}
				if strings.HasPrefix(host, "v") {
					if !bakneyIPFuture.MatchString(host) {
						return result, errors.New("IPvFuture address is invalid")
					}
				} else if ip := net.ParseIP(host); ip == nil || ip.To4() != nil && !strings.Contains(host, ":") {
					return result, errors.New("invalid bracketed host")
				}
			}
		}
	}
	if before, fragment, found := strings.Cut(url, "#"); found {
		url, result.fragment = before, fragment
	}
	if before, query, found := strings.Cut(url, "?"); found {
		url, result.query = before, query
	}
	result.path = url
	return result, nil
}

func (p pythonSplit) hostinfo() (string, string, bool) {
	hostinfo := p.netloc[strings.LastIndex(p.netloc, "@")+1:]
	var host, port string
	if _, bracketed, found := strings.Cut(hostinfo, "["); found {
		var rest string
		host, rest, _ = strings.Cut(bracketed, "]")
		_, port, _ = strings.Cut(rest, ":")
	} else {
		host, port, _ = strings.Cut(hostinfo, ":")
	}
	return host, port, port != ""
}

// normalizeBakneyOrigin is protocol.normalize_origin; every rejection is
// configuration_required (409).
func normalizeBakneyOrigin(value string, settings BakneySSOSettings) (string, error) {
	invalid := ssoError("configuration_required", 409)
	if strings.ContainsAny(value, "\\%") || strings.IndexFunc(value, pythonIsSpace) >= 0 {
		return "", invalid
	}
	for _, r := range value {
		if r >= utf8.RuneSelf {
			return "", ErrBakneySSOUnsupported
		}
	}
	parts, err := pythonURLSplit(value)
	if err != nil {
		return "", invalid
	}
	host, portText, hasPort := parts.hostinfo()
	host = strings.ToLower(host)
	schemeAllowed := parts.scheme == "https" || parts.scheme == "http" && settings.developmentHTTP()
	if !schemeAllowed || host == "" || strings.Contains(parts.netloc, "@") || parts.path != "" && parts.path != "/" ||
		parts.query != "" || parts.fragment != "" {
		return "", invalid
	}
	port := -1
	if hasPort {
		for _, c := range portText {
			if c < '0' || c > '9' {
				return "", invalid
			}
		}
		number, err := strconv.Atoi(portText)
		if err != nil || number > 65535 {
			return "", invalid
		}
		port = number
	}
	if port == 0 {
		return "", invalid
	}
	// str.encode('idna') ASCII fast path: every label but the last 1..63
	// characters, the last under 64.
	labels := strings.Split(host, ".")
	for index, label := range labels {
		if index < len(labels)-1 && (len(label) == 0 || len(label) > 63) || len(label) > 63 {
			return "", invalid
		}
	}
	if strings.Contains(host, ":") {
		host = "[" + host + "]"
	}
	defaultPort := 80
	if parts.scheme == "https" {
		defaultPort = 443
	}
	result := parts.scheme + "://" + host
	if port > 0 && port != defaultPort {
		result += ":" + strconv.Itoa(port)
	}
	return result, nil
}

// bakneyAuthority is protocol.authority(): the configured API base.
func bakneyAuthority(settings BakneySSOSettings) (string, error) {
	value := strings.TrimRight(settings.APIBase, "/")
	parts, err := pythonURLSplit(value)
	if err != nil {
		return "", err // ValueError from urlsplit is not an SSOError.
	}
	base, err := normalizeBakneyOrigin(parts.scheme+"://"+parts.netloc, settings)
	if err != nil {
		return "", err
	}
	if parts.query != "" || parts.fragment != "" || !bakneyPath.MatchString(parts.path) {
		return "", ssoError("configuration_required", 409)
	}
	return base + parts.path, nil
}

func bakneyUIOrigin(settings BakneySSOSettings) (string, error) {
	return normalizeBakneyOrigin(settings.UIOrigin, settings)
}

// bakneyOrigin is protocol.origin(): APP_URL, an HTTPS DNS origin on port 443
// outside development.
func bakneyOrigin(settings BakneySSOSettings) (string, error) {
	value, err := normalizeBakneyOrigin(strings.TrimRight(settings.AppURL, "/"), settings)
	if err != nil || settings.developmentHTTP() {
		return value, err
	}
	parts, _ := pythonURLSplit(value)
	host, _, hasPort := parts.hostinfo()
	host = strings.ToLower(host)
	digits := strings.ReplaceAll(host, ".", "")
	allDigits := digits != ""
	for _, c := range digits {
		if c < '0' || c > '9' {
			allDigits = false
		}
	}
	if hasPort || host == "" || !strings.Contains(host, ".") || allDigits || strings.HasSuffix(host, ".") || !bakneyDNSHost.MatchString(host) {
		return "", ssoError("configuration_required", 409)
	}
	return value, nil
}

func canonicalUUID(value any) bool {
	text, ok := value.(string)
	return ok && bakneyUUID.MatchString(text)
}

// bakneyDigest is protocol.digest: SHA-256 hex of the ASCII value.
func bakneyDigest(value string) (string, error) {
	for i := 0; i < len(value); i++ {
		if value[i] >= utf8.RuneSelf {
			return "", ErrBakneySSOUnsupported
		}
	}
	sum := sha256.Sum256([]byte(value))
	return hex.EncodeToString(sum[:]), nil
}

func bakneyFernetKey(secret string) [32]byte {
	return sha256.Sum256([]byte(bakneyCipherLabel + secret))
}

// bakneyEncrypt is protocol.encrypt: Fernet keyed by
// sha256('assozeta:bakney-sso:v1:' + SECRET_KEY).
func bakneyEncrypt(plain string, settings BakneySSOSettings, now time.Time) (string, error) {
	if len(settings.SecretKeys) == 0 {
		return "", errors.New("bakney sso requires SECRET_KEY")
	}
	key := bakneyFernetKey(settings.SecretKeys[0])
	block, err := aes.NewCipher(key[16:])
	if err != nil {
		return "", err
	}
	iv := make([]byte, aes.BlockSize)
	if _, err := io.ReadFull(rand.Reader, iv); err != nil {
		return "", err
	}
	pad := aes.BlockSize - len(plain)%aes.BlockSize
	padded := append([]byte(plain), bytes.Repeat([]byte{byte(pad)}, pad)...)
	ciphertext := make([]byte, len(padded))
	cipher.NewCBCEncrypter(block, iv).CryptBlocks(ciphertext, padded)
	token := make([]byte, 0, 1+8+16+len(ciphertext)+32)
	token = append(token, 0x80)
	token = binary.BigEndian.AppendUint64(token, uint64(now.Unix()))
	token = append(token, iv...)
	token = append(token, ciphertext...)
	mac := hmac.New(sha256.New, key[:16])
	mac.Write(token)
	token = mac.Sum(token)
	return base64.URLEncoding.EncodeToString(token), nil
}

// bakneyDecrypt is protocol.decrypt: SECRET_KEY, then each fallback; no key
// opening the token is configuration_required.
func bakneyDecrypt(token string, settings BakneySSOSettings) (string, error) {
	for _, secret := range settings.SecretKeys {
		if plain, ok := bakneyFernetOpen(token, secret); ok {
			return plain, nil
		}
	}
	return "", ssoError("configuration_required", 409)
}

func bakneyFernetOpen(token, secret string) (string, bool) {
	key := bakneyFernetKey(secret)
	data, err := base64.URLEncoding.DecodeString(token)
	if err != nil || len(data) < 1+8+16+16+32 || data[0] != 0x80 || (len(data)-1-8-16-32)%aes.BlockSize != 0 {
		return "", false
	}
	body, tag := data[:len(data)-32], data[len(data)-32:]
	mac := hmac.New(sha256.New, key[:16])
	mac.Write(body)
	if !hmac.Equal(mac.Sum(nil), tag) {
		return "", false
	}
	block, err := aes.NewCipher(key[16:])
	if err != nil {
		return "", false
	}
	iv, ciphertext := body[9:25], body[25:]
	plain := make([]byte, len(ciphertext))
	cipher.NewCBCDecrypter(block, iv).CryptBlocks(plain, ciphertext)
	pad := int(plain[len(plain)-1])
	if pad == 0 || pad > aes.BlockSize {
		return "", false
	}
	for _, b := range plain[len(plain)-pad:] {
		if int(b) != pad {
			return "", false
		}
	}
	plain = plain[:len(plain)-pad]
	if !utf8.Valid(plain) {
		return "", false
	}
	return string(plain), true
}

// bakneyBinding holds the signed fields in contract order.
type bakneyBinding struct {
	PairingID, AssociationID, InstanceID, Origin, CallbackURI, Generation, Nonce, ExpiresAt string
}

// bakneySignature is protocol.signature: HMAC-SHA256 keyed by the decoded
// 32-byte secret over VERSION, phase and the binding, LF separated.
func bakneySignature(secret, phase string, binding bakneyBinding) (string, error) {
	key, err := base64.URLEncoding.DecodeString(secret + "=")
	if err != nil {
		return "", ErrBakneySSOUnsupported
	}
	fields := []string{BakneySSOVersion, phase, binding.PairingID, binding.AssociationID, binding.InstanceID, binding.Origin,
		binding.CallbackURI, binding.Generation, binding.Nonce, binding.ExpiresAt}
	message := strings.Join(fields, "\n")
	for i := 0; i < len(message); i++ {
		if message[i] >= utf8.RuneSelf {
			return "", ErrBakneySSOUnsupported
		}
	}
	mac := hmac.New(sha256.New, key)
	mac.Write([]byte(message))
	return hex.EncodeToString(mac.Sum(nil)), nil
}

// tokenURLSafe is secrets.token_urlsafe(32).
func tokenURLSafe() (string, error) {
	raw := make([]byte, 32)
	if _, err := io.ReadFull(rand.Reader, raw); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(raw), nil
}

// pythonJSONStrings is json.dumps of a str-valued dict (default separators,
// ensure_ascii) in insertion order, as requests encodes json=.
func pythonJSONStrings(keys []string, values map[string]string) []byte {
	object := make(domain.RequestObject, 0, len(keys))
	for _, key := range keys {
		object = append(object, domain.RequestField{Name: key, Value: values[key]})
	}
	return dto.PythonJSONDumps(object)
}

// BakneyAuthorityClient is protocol.upstream's transport: requests.Session
// with trust_env=False (no proxies or netrc), verified TLS, no redirects,
// (3, 8) second timeouts and the library's default headers.
type BakneyAuthorityClient struct {
	client *http.Client
}

func NewBakneyAuthorityClient() *BakneyAuthorityClient {
	transport := &http.Transport{
		Proxy:                 nil,
		DialContext:           (&net.Dialer{Timeout: 3 * time.Second}).DialContext,
		TLSHandshakeTimeout:   8 * time.Second,
		ResponseHeaderTimeout: 8 * time.Second,
		DisableCompression:    true,
		MaxIdleConns:          4,
		IdleConnTimeout:       30 * time.Second,
	}
	return &BakneyAuthorityClient{client: &http.Client{Transport: transport, Timeout: 30 * time.Second,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}
}

// CloseIdleConnections releases pooled connections at shutdown.
func (c *BakneyAuthorityClient) CloseIdleConnections() { c.client.CloseIdleConnections() }

// post sends one JSON request and returns the decoded protocol object.
func (c *BakneyAuthorityClient) post(ctx context.Context, url string, body []byte) (domain.RequestObject, error) {
	unavailable := ssoError("bakney_unavailable", 503)
	handoff.MarkEffect(ctx, "authority request")
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, unavailable
	}
	request.Header = http.Header{"User-Agent": {bakneyUserAgent}, "Accept-Encoding": {"gzip, deflate"}, "Accept": {"application/json"},
		"Connection": {"keep-alive"}, "Content-Type": {"application/json"}}
	response, err := c.client.Do(request)
	if err != nil {
		if ctxErr := ctx.Err(); ctxErr != nil {
			return nil, ctxErr
		}
		return nil, unavailable
	}
	defer response.Body.Close()
	switch {
	case response.StatusCode == 200:
	case response.StatusCode == 401:
		return nil, ssoError("pairing_rejected", 403)
	case response.StatusCode == 400 || response.StatusCode == 403 || response.StatusCode == 409 || response.StatusCode == 410:
		return nil, ssoError("handoff_rejected", 400)
	default:
		return nil, unavailable
	}
	reader, err := contentDecoder(response.Body, response.Header.Values("Content-Encoding"))
	if err != nil {
		return nil, unavailable
	}
	raw, err := io.ReadAll(io.LimitReader(reader, 8193))
	if err != nil {
		if ctxErr := ctx.Err(); ctxErr != nil {
			return nil, ctxErr
		}
		return nil, unavailable
	}
	if len(raw) > 8192 {
		return nil, ssoError("invalid_response", 502)
	}
	raw = bytes.TrimPrefix(raw, []byte("\xef\xbb\xbf"))
	if !utf8.Valid(raw) {
		return nil, unavailable
	}
	value, err := dto.ParseReferenceJSON(raw)
	if err != nil {
		return nil, unavailable
	}
	object, ok := value.(domain.RequestObject)
	if !ok {
		return nil, ssoError("invalid_response", 502)
	}
	if protocol, _ := object.Lookup("protocol"); protocol != BakneySSOVersion {
		return nil, ssoError("invalid_response", 502)
	}
	return object, nil
}

// contentDecoder is urllib3's decode_content for gzip and deflate (zlib or
// raw); other codings pass through undecoded.
func contentDecoder(body io.Reader, values []string) (io.Reader, error) {
	var codings []string
	for _, value := range values {
		for _, item := range strings.Split(value, ",") {
			if item = strings.ToLower(strings.TrimSpace(item)); item != "" {
				codings = append(codings, item)
			}
		}
	}
	reader := body
	for index := len(codings) - 1; index >= 0; index-- {
		switch codings[index] {
		case "gzip", "x-gzip":
			decoded, err := gzip.NewReader(reader)
			if err != nil {
				return nil, err
			}
			reader = decoded
		case "deflate":
			buffered := &bytes.Buffer{}
			if _, err := io.Copy(buffered, io.LimitReader(reader, 1<<20)); err != nil {
				return nil, err
			}
			if decoded, err := zlib.NewReader(bytes.NewReader(buffered.Bytes())); err == nil {
				reader = decoded
			} else {
				reader = flate.NewReader(bytes.NewReader(buffered.Bytes()))
			}
		default:
			if len(codings) > 1 {
				return reader, nil
			}
		}
	}
	return reader, nil
}

// bakneyPythonInt reports whether a parsed JSON value is a Python int (a
// number without fraction or exponent) and returns its value.
func bakneyPythonInt(value any) (*big.Int, bool) {
	number, ok := value.(json.Number)
	if !ok || strings.ContainsAny(string(number), ".eE") {
		return nil, false
	}
	return new(big.Int).SetString(string(number), 10)
}
