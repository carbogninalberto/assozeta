package googleapi

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"math"
	"math/big"
	"os"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"golang.org/x/text/encoding/charmap"
)

// Scopes is settings.GOOGLE_CALENDAR_SCOPES.
var Scopes = []string{"https://www.googleapis.com/auth/calendar", "https://www.googleapis.com/auth/userinfo.email",
	"https://www.googleapis.com/auth/userinfo.profile", "openid"}

// Environment is the process environment oauthlib consults.
type Environment struct{ InsecureTransport, RelaxTokenScope, StrictTokenType bool }

// ClientConfig is google_auth_oauthlib.helpers.session_from_client_config's
// validated "web"/"installed" section.
type ClientConfig struct {
	AuthURI, TokenURI, ClientID string
	ClientSecret                any
	clientSecretPresent         bool
	config                      domain.RequestObject
}

// LoadClientSecrets is Flow.from_client_secrets_file.
func LoadClientSecrets(path string) (*ClientConfig, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, &PythonError{Message: "FileNotFoundError: " + path}
	}
	if !utf8.Valid(raw) {
		return nil, &PythonError{Message: "UnicodeDecodeError"}
	}
	parsed, err := dto.ParsePythonJSON(string(raw))
	if err != nil {
		return nil, &PythonError{Message: "JSONDecodeError"}
	}
	root, ok := parsed.(domain.RequestObject)
	if !ok {
		return nil, ErrNotPorted
	}
	section, present := lookup(root, "web")
	if !present {
		if section, present = lookup(root, "installed"); !present {
			return nil, &PythonError{Message: "ValueError: Client secrets must be for a web or installed app."}
		}
	}
	config, ok := section.(domain.RequestObject)
	if !ok {
		return nil, ErrNotPorted
	}
	result := &ClientConfig{config: config}
	for name, target := range map[string]*string{"auth_uri": &result.AuthURI, "token_uri": &result.TokenURI, "client_id": &result.ClientID} {
		value, present := lookup(config, name)
		if !present {
			return nil, &PythonError{Message: "ValueError: Client secrets is not in the correct format."}
		}
		text, ok := value.(string)
		if !ok {
			return nil, ErrNotPorted
		}
		*target = text
	}
	result.ClientSecret, result.clientSecretPresent = lookup(config, "client_secret")
	if !textOrNone(result.ClientSecret) {
		return nil, ErrNotPorted
	}
	return result, nil
}

// secure is oauthlib's is_secure_transport.
func secure(uri string, environment Environment) bool {
	return environment.InsecureTransport || strings.HasPrefix(strings.ToLower(uri), "https://")
}

// plainURI accepts the URIs whose urlparse/urlunparse round trip is the identity
// and that carry no query, parameters or fragment of their own.
func plainURI(uri string) bool {
	return (strings.HasPrefix(uri, "https://") || strings.HasPrefix(uri, "http://")) && !strings.ContainsAny(uri, "?#;")
}

const stateCharacters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

// generateToken is oauthlib.common.generate_token(30) (SystemRandom choices).
func generateToken() (string, error) {
	out := make([]byte, 30)
	limit := big.NewInt(int64(len(stateCharacters)))
	for i := range out {
		n, err := rand.Int(rand.Reader, limit)
		if err != nil {
			return "", err
		}
		out[i] = stateCharacters[n.Int64()]
	}
	return string(out), nil
}

// AuthorizationURL is Flow.authorization_url(access_type='offline',
// include_granted_scopes='true') without PKCE (from_client_config passes
// autogenerate_code_verifier=None): oauthlib's prepare_grant_uri.
func (c *ClientConfig) AuthorizationURL(redirect string, environment Environment) (string, string, error) {
	state, err := generateToken()
	if err != nil {
		return "", "", err
	}
	if !secure(c.AuthURI, environment) {
		return "", "", &PythonError{Message: "InsecureTransportError: OAuth 2 MUST utilize https."}
	}
	if !plainURI(c.AuthURI) {
		return "", "", ErrNotPorted
	}
	pairs := [][2]string{{"response_type", "code"}, {"client_id", c.ClientID}}
	if redirect != "" {
		pairs = append(pairs, [2]string{"redirect_uri", redirect})
	}
	pairs = append(pairs, [2]string{"scope", strings.Join(Scopes, " ")}, [2]string{"state", state},
		[2]string{"access_type", "offline"}, [2]string{"include_granted_scopes", "true"})
	return c.AuthURI + "?" + urlencode(pairs), state, nil
}

// FetchToken is Flow.fetch_token(code=code) then flow.credentials.to_json():
// requests_oauthlib posts grant_type, code and redirect_uri with HTTP Basic
// client authentication; oauthlib parses and validates the answer.
func (c *ClientConfig) FetchToken(ctx context.Context, transport *Transport, code, redirect string, environment Environment,
	now func() time.Time) (string, error) {
	if !c.clientSecretPresent {
		return "", &PythonError{Message: "KeyError: 'client_secret'"}
	}
	if !secure(c.TokenURI, environment) {
		return "", &PythonError{Message: "InsecureTransportError: OAuth 2 MUST utilize https."}
	}
	if code == "" {
		return "", &PythonError{Message: "ValueError: Please supply either code or authorization_response parameters."}
	}
	if !plainURI(c.TokenURI) {
		return "", ErrNotPorted
	}
	pairs := [][2]string{{"grant_type", "authorization_code"}, {"code", code}}
	if redirect != "" {
		pairs = append(pairs, [2]string{"redirect_uri", redirect})
	}
	body := []byte(urlencode(pairs))
	secret, _ := c.ClientSecret.(string)
	user, err1 := charmap.ISO8859_1.NewEncoder().String(c.ClientID)
	password, err2 := charmap.ISO8859_1.NewEncoder().String(secret)
	if err1 != nil || err2 != nil {
		return "", &PythonError{Message: "UnicodeEncodeError"}
	}
	basic := base64.StdEncoding.EncodeToString([]byte(user + ":" + password))
	headers := requestsHeaders("application/json", []Header{{"Content-Type", "application/x-www-form-urlencoded"}}, len(body),
		Header{"Authorization", "Basic " + basic})
	answer, err := transport.Do(ctx, "POST", c.TokenURI, headers, body)
	if err != nil {
		return "", err
	}
	requested := time.Now
	if now != nil {
		requested = now
	}
	return c.tokenCredentials(answer, environment, requested())
}

func (c *ClientConfig) tokenCredentials(answer *Response, environment Environment, at time.Time) (string, error) {
	parsed, err := parseJSON(answer.Body)
	if err != nil {
		// oauthlib falls back to a form-encoded body: not ported.
		return "", ErrNotPorted
	}
	params, ok := parsed.(domain.RequestObject)
	if !ok {
		return "", ErrNotPorted
	}
	var granted []string
	scopeChanged := false
	if scope, present := lookup(params, "scope"); present {
		// scope_to_list makes any string a non-empty list, so OAuth2Token compares it.
		switch v := scope.(type) {
		case nil:
		case string:
			granted = strings.Split(strings.TrimSpace(v), " ")
			scopeChanged = !sameSet(granted, Scopes)
		default:
			return "", ErrNotPorted
		}
	}
	// parse_expires
	var expiresIn int64
	hasExpiresIn := false
	if value, present := lookup(params, "expires_in"); present && value != nil {
		switch v := value.(type) {
		case string:
			number, valid := pythonInt(v)
			if !valid {
				return "", &PythonError{Message: "ValueError: expires_in must be an int"}
			}
			if !number.IsInt64() {
				return "", ErrNotPorted
			}
			expiresIn, hasExpiresIn = number.Int64(), true
		case jsonNumber:
			f, err := v.Float64()
			if err != nil || math.IsInf(f, 0) || math.Abs(f) > 1e15 {
				return "", ErrNotPorted
			}
			expiresIn, hasExpiresIn = int64(f), true
		default:
			return "", ErrNotPorted
		}
	}
	if _, present := lookup(params, "expires_at"); present {
		return "", ErrNotPorted
	}
	// validate_token_parameters
	if _, present := lookup(params, "error"); present {
		return "", &PythonError{Message: "OAuth2Error", Data: params}
	}
	token, present := lookup(params, "access_token")
	if !present {
		return "", &PythonError{Message: "MissingTokenError: Missing access token parameter."}
	}
	if _, present := lookup(params, "token_type"); !present && environment.StrictTokenType {
		return "", &PythonError{Message: "MissingTokenTypeError"}
	}
	if scopeChanged && !environment.RelaxTokenScope {
		return "", &PythonError{Message: "Warning: Scope has changed."}
	}
	if _, ok := token.(string); !ok {
		return "", ErrNotPorted
	}
	refresh, _ := lookup(params, "refresh_token")
	if !textOrNone(refresh) {
		return "", ErrNotPorted
	}
	// credentials_from_session: expiry = utcfromtimestamp(token['expires_at']),
	// expires_at = round(time.time()) + expires_in when expires_in is truthy.
	if !hasExpiresIn || expiresIn == 0 {
		return "", &PythonError{Message: "KeyError: 'expires_at'"}
	}
	stamp := int64(math.RoundToEven(float64(at.UnixNano())/1e9)) + expiresIn
	expiry := time.Unix(stamp, 0).UTC()
	if expiry.Year() < 1 || expiry.Year() > 9999 {
		return "", ErrNotPorted
	}
	secret, _ := lookup(c.config, "client_secret")
	credentials := &Credentials{Token: token, RefreshToken: refresh, TokenURI: c.TokenURI, ClientID: c.ClientID, ClientSecret: secret,
		Scopes: append([]string(nil), Scopes...), Universe: defaultUniverse, Account: "", Expiry: &expiry}
	return credentials.ToJSON(), nil
}

func sameSet(left, right []string) bool {
	set := map[string]bool{}
	for _, item := range left {
		set[item] = true
	}
	other := map[string]bool{}
	for _, item := range right {
		other[item] = true
	}
	if len(set) != len(other) {
		return false
	}
	for item := range set {
		if !other[item] {
			return false
		}
	}
	return true
}
