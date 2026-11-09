package googleapi

import (
	"context"
	"encoding/json"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// refreshThreshold is google.auth._helpers.REFRESH_THRESHOLD.
const refreshThreshold = 3*time.Minute + 45*time.Second

const defaultUniverse = "googleapis.com"

// Credentials is google.oauth2.credentials.Credentials built by
// from_authorized_user_info. Text fields hold a string or nil (other JSON
// types are refused when loading).
type Credentials struct {
	Token, RefreshToken, ClientID, ClientSecret any
	TokenURI                                    string
	Scopes                                      []string // nil is None
	Rapt, Quota                                 any
	Universe                                    string
	Account                                     any
	Expiry                                      *time.Time
}

// strptime is datetime.strptime(text, "%Y-%m-%dT%H:%M:%S") (literals match case-insensitively).
var strptime = regexp.MustCompile(`(?i)^(\d\d\d\d)-(1[0-2]|0[1-9]|[1-9])-(3[01]|[12]\d|0[1-9]|[1-9]| [1-9])T(2[0-3]|[0-1]\d|\d):([0-5]\d|\d):(6[0-1]|[0-5]\d|\d)$`)

func textOrNone(value any) bool {
	switch value.(type) {
	case nil, string:
		return true
	}
	return false
}

// LoadCredentials is Credentials.from_authorized_user_info(json.loads(stored)).
func LoadCredentials(stored any, now time.Time) (*Credentials, error) {
	text, ok := stored.(string)
	if !ok {
		return nil, &PythonError{Message: "TypeError: the JSON object must be str"}
	}
	parsed, err := dto.ParsePythonJSON(text)
	if err != nil {
		return nil, &PythonError{Message: "JSONDecodeError"}
	}
	info, ok := parsed.(domain.RequestObject)
	if !ok {
		return nil, &PythonError{Message: "AttributeError: info has no keys"}
	}
	for _, key := range []string{"refresh_token", "client_id", "client_secret"} {
		if _, present := lookup(info, key); !present {
			return nil, &PythonError{Message: "ValueError: Authorized user info was not in the expected format, missing fields " + key + "."}
		}
	}
	get := func(name string) any { value, _ := lookup(info, name); return value }
	credentials := &Credentials{Token: get("token"), RefreshToken: get("refresh_token"), ClientID: get("client_id"),
		ClientSecret: get("client_secret"), TokenURI: TokenURI, Rapt: get("rapt_token"), Quota: get("quota_project_id"),
		Universe: defaultUniverse, Account: ""}
	if account, present := lookup(info, "account"); present {
		credentials.Account = account
	}
	for _, value := range []any{credentials.Token, credentials.RefreshToken, credentials.ClientID, credentials.ClientSecret,
		credentials.Rapt, credentials.Quota, credentials.Account} {
		if !textOrNone(value) {
			return nil, ErrNotPorted
		}
	}
	if universe := get("universe_domain"); universe != nil {
		if text, ok := universe.(string); !ok || (text != "" && text != defaultUniverse) {
			return nil, ErrNotPorted
		}
	}
	if boundary := get("trust_boundary"); boundary != nil {
		return nil, ErrNotPorted
	}
	if expiry := get("expiry"); truthy(expiry) {
		text, ok := expiry.(string)
		if !ok {
			return nil, &PythonError{Message: "AttributeError: expiry has no rstrip"}
		}
		at, err := parseExpiry(text)
		if err != nil {
			return nil, err
		}
		credentials.Expiry = at
	} else {
		at := now.UTC().Truncate(time.Microsecond).Add(-refreshThreshold)
		credentials.Expiry = &at
	}
	if scopes, present := lookup(info, "scopes"); present && scopes != nil {
		switch v := scopes.(type) {
		case string:
			credentials.Scopes = strings.Split(v, " ")
		case []any:
			credentials.Scopes = []string{}
			for _, item := range v {
				text, ok := item.(string)
				if !ok {
					return nil, ErrNotPorted
				}
				credentials.Scopes = append(credentials.Scopes, text)
			}
		default:
			return nil, ErrNotPorted
		}
	}
	return credentials, nil
}

func parseExpiry(text string) (*time.Time, error) {
	text = strings.TrimRight(text, "Z")
	text, _, _ = strings.Cut(text, ".")
	match := strptime.FindStringSubmatch(text)
	invalid := &PythonError{Message: "ValueError: time data does not match format"}
	if match == nil {
		return nil, invalid
	}
	number := func(i int) int { value, _ := strconv.Atoi(strings.TrimSpace(match[i])); return value }
	year, month, day := number(1), number(2), number(3)
	hour, minute, second := number(4), number(5), number(6)
	if second > 59 || year < 1 {
		return nil, invalid
	}
	at := time.Date(year, time.Month(month), day, hour, minute, second, 0, time.UTC)
	if at.Day() != day {
		return nil, invalid
	}
	return &at, nil
}

// Expired is credentials.expired: utcnow() >= expiry - REFRESH_THRESHOLD.
func (c *Credentials) Expired(now time.Time) bool {
	return c.Expiry != nil && !now.UTC().Before(c.Expiry.Add(-refreshThreshold))
}

// Valid is token is not None and not expired.
func (c *Credentials) Valid(now time.Time) bool { return c.Token != nil && !c.Expired(now) }

// ToJSON is Credentials.to_json(): json.dumps of the non-None fields in order.
func (c *Credentials) ToJSON() string {
	object := domain.RequestObject{}
	add := func(name string, value any) {
		if value != nil {
			object = append(object, domain.RequestField{Name: name, Value: value})
		}
	}
	add("token", c.Token)
	add("refresh_token", c.RefreshToken)
	add("token_uri", c.TokenURI)
	add("client_id", c.ClientID)
	add("client_secret", c.ClientSecret)
	if c.Scopes != nil {
		scopes := make([]any, len(c.Scopes))
		for i, scope := range c.Scopes {
			scopes[i] = scope
		}
		add("scopes", scopes)
	}
	add("rapt_token", c.Rapt)
	add("universe_domain", c.Universe)
	add("account", c.Account)
	if c.Expiry != nil {
		add("expiry", isoformat(*c.Expiry)+"Z")
	}
	return string(dto.PythonJSONDumps(object))
}

// refreshProfile is the transport a refresh goes through: requests for
// get_google_credentials, httplib2 for google_auth_httplib2.AuthorizedHttp.
type refreshProfile int

const (
	viaRequests refreshProfile = iota
	viaHTTPLib2
)

var retryableStatus = map[int]bool{500: true, 503: true, 408: true, 429: true}

// Refresh is Credentials.refresh (reauth.refresh_grant with the token
// endpoint's retries: three attempts, exponential backoff from 1 s with 10 % jitter).
func (c *Credentials) Refresh(ctx context.Context, transport *Transport, profile refreshProfile, now func() time.Time) error {
	if c.RefreshToken == nil || c.ClientID == nil || c.ClientSecret == nil {
		return &PythonError{Message: "RefreshError: The credentials do not contain the necessary fields need to refresh the access token. " +
			"You must specify refresh_token, token_uri, client_id, and client_secret."}
	}
	pairs := [][2]string{{"grant_type", "refresh_token"}, {"client_id", c.ClientID.(string)}, {"client_secret", c.ClientSecret.(string)},
		{"refresh_token", c.RefreshToken.(string)}}
	if len(c.Scopes) > 0 {
		pairs = append(pairs, [2]string{"scope", strings.Join(c.Scopes, " ")})
	}
	if rapt, _ := c.Rapt.(string); rapt != "" {
		pairs = append(pairs, [2]string{"rapt", rapt})
	}
	body := []byte(urlencode(pairs))
	var headers []Header
	if profile == viaRequests {
		headers = requestsHeaders("*/*", []Header{{"Content-Type", "application/x-www-form-urlencoded"}, {"x-goog-api-client", authMetricHeader}},
			len(body))
	} else {
		headers = []Header{{"Content-Length", strconv.Itoa(len(body))}, {"content-type", "application/x-www-form-urlencoded"},
			{"x-goog-api-client", authMetricHeader}, {"user-agent", httplib2Agent}, {"accept-encoding", "gzip, deflate"}}
	}
	var data any
	var status int
	wait := 1.0
	for attempt := 0; attempt < 3; attempt++ {
		if attempt > 0 {
			jitter := wait * 0.1
			delay := wait - jitter + transport.random()*2*jitter
			if err := transport.sleep(ctx, time.Duration(delay*float64(time.Second))); err != nil {
				return err
			}
			wait *= 2
		}
		answer, err := transport.Do(ctx, "POST", TokenURI, headers, body)
		if err != nil {
			return err
		}
		status = answer.Status
		parsed, err := parseJSON(answer.Body)
		if _, decode := err.(*PythonError); decode {
			return err
		}
		if err != nil {
			data = string(answer.Body)
		} else {
			data = parsed
		}
		if status == 200 || !canRetry(status, data) {
			break
		}
	}
	if status != 200 {
		return refreshError(data)
	}
	object, ok := data.(domain.RequestObject)
	if !ok {
		return &PythonError{Message: "TypeError: response data is not a mapping"}
	}
	token, present := lookup(object, "access_token")
	if !present {
		return &PythonError{Message: "RefreshError: No access token in response.", Data: object}
	}
	if _, ok := token.(string); !ok {
		return ErrNotPorted
	}
	if refresh, present := lookup(object, "refresh_token"); present {
		if !textOrNone(refresh) {
			return ErrNotPorted
		}
		c.RefreshToken = refresh
	}
	var expiry *time.Time
	if expiresIn, _ := lookup(object, "expires_in"); expiresIn != nil {
		if text, ok := expiresIn.(string); ok {
			value, valid := pythonInt(text)
			if !valid {
				return &PythonError{Message: "ValueError: invalid literal for int()"}
			}
			if !value.IsInt64() {
				return ErrNotPorted
			}
			expiresIn = value.Int64()
		}
		delta, err := seconds(expiresIn)
		if err != nil {
			return err
		}
		at := now().UTC().Truncate(time.Microsecond).Add(delta)
		expiry = &at
	}
	c.Token, c.Expiry = token, expiry
	return nil
}

// canRetry is google.oauth2._client._can_retry.
func canRetry(status int, data any) bool {
	if retryableStatus[status] {
		return true
	}
	object, ok := data.(domain.RequestObject)
	if !ok {
		return false
	}
	description, _ := lookup(object, "error_description")
	code, _ := lookup(object, "error")
	if !truthy(description) {
		description = ""
	}
	if !truthy(code) {
		code = ""
	}
	descriptionText, ok1 := description.(string)
	codeText, ok2 := code.(string)
	if !ok1 || !ok2 {
		return false
	}
	for _, retryable := range []string{"internal_failure", "server_error", "temporarily_unavailable"} {
		if codeText == retryable || descriptionText == retryable {
			return true
		}
	}
	return false
}

// refreshError is reauth.refresh_grant's error path (_handle_error_response).
func refreshError(data any) error {
	object, ok := data.(domain.RequestObject)
	if !ok {
		text, _ := data.(string)
		return &PythonError{Message: "RefreshError: " + text}
	}
	if code, _ := lookup(object, "error"); code == "invalid_grant" {
		if subtype, _ := lookup(object, "error_subtype"); subtype == "invalid_rapt" || subtype == "rapt_required" {
			return &PythonError{Message: "RefreshError: Reauthentication is needed. Please run `gcloud auth application-default login` to reauthenticate."}
		}
	}
	code, present := lookup(object, "error")
	message := "RefreshError: "
	if present {
		description, _ := lookup(object, "error_description")
		message += pythonStr(code) + ": " + pythonStr(description)
	} else {
		message += string(dto.PythonJSONDumps(object))
	}
	return &PythonError{Message: message, Data: object}
}

// pythonStr is str() of a scalar parsed JSON value (containers by their JSON text).
func pythonStr(value any) string {
	switch v := value.(type) {
	case nil:
		return "None"
	case bool:
		if v {
			return "True"
		}
		return "False"
	case string:
		return v
	case json.Number:
		if number, ok := new(big.Int).SetString(string(v), 10); ok {
			return number.String()
		}
		return string(v)
	}
	return string(dto.PythonJSONDumps(value))
}
