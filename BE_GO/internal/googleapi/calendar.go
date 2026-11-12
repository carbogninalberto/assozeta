package googleapi

import (
	"context"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// HTTPError is googleapiclient.errors.HttpError (a status >= 300).
type HTTPError struct {
	Status int
	Reason string
	Body   []byte
	URI    string
}

func (e *HTTPError) Error() string {
	return "google: HttpError " + strconv.Itoa(e.Status) + " when requesting " + e.URI
}

// Mentions is `text in str(error)`: str() is '<HttpError status when
// requesting uri returned "reason". Details: "details">', with reason and
// details taken from the body by HttpError._get_reason.
func (e *HTTPError) Mentions(text string) bool {
	reason := any(e.Reason)
	var details any = ""
	if data, err := parseJSON(e.Body); err == nil || !isDecodeError(err) {
		if err != nil {
			data = string(e.Body)
		}
		reason, details = httpErrorReason(data, reason)
	}
	if reason == nil {
		reason = ""
	}
	reasonText, ok := reason.(string)
	if !ok {
		// reason.strip() raises AttributeError inside the HttpError constructor.
		return false
	}
	if strings.Contains(strings.TrimSpace(reasonText), text) || strings.Contains(e.URI, text) {
		return true
	}
	return truthy(details) && containsString(details, text)
}

func isDecodeError(err error) bool {
	failure, ok := err.(*PythonError)
	return ok && failure.Message == "UnicodeDecodeError"
}

// httpErrorReason is the body part of HttpError._get_reason (ValueError,
// KeyError and TypeError keep what was assigned so far).
func httpErrorReason(data any, reason any) (any, any) {
	switch v := data.(type) {
	case domain.RequestObject:
		inner, present := lookup(v, "error")
		object, ok := inner.(domain.RequestObject)
		if !present || !ok {
			return reason, ""
		}
		message, present := lookup(object, "message")
		if !present {
			return reason, ""
		}
		for _, keyword := range []string{"detail", "details", "errors", "message"} {
			if value, present := lookup(object, keyword); present {
				return message, value
			}
		}
		return message, ""
	case []any:
		if len(v) == 0 {
			return reason, v
		}
		first, ok := v[0].(domain.RequestObject)
		if !ok {
			return reason, ""
		}
		inner, _ := lookup(first, "error")
		object, ok := inner.(domain.RequestObject)
		if !ok {
			return reason, ""
		}
		message, present := lookup(object, "message")
		if !present {
			return reason, ""
		}
		if value, present := lookup(object, "details"); present {
			return message, value
		}
		return message, ""
	}
	return reason, data
}

// Session is build('calendar', 'v3', credentials=...) over
// google_auth_httplib2.AuthorizedHttp: before each request invalid
// credentials are refreshed (in memory), and a 401 answer refreshes and
// retries, at most twice per call.
type Session struct {
	transport   *Transport
	credentials *Credentials
	now         func() time.Time
}

func NewSession(transport *Transport, credentials *Credentials, now func() time.Time) *Session {
	return &Session{transport: transport, credentials: credentials, now: now}
}

// Param is one query parameter in the order googleapiclient adds them.
type Param struct{ Name, Value string }

// Call is one discovery method call. Raw methods (no response schema:
// calendars.delete, calendarList.delete) use RawModel: Accept */*, no alt=json.
type Call struct {
	Method, Path string
	Query        []Param
	Body         any
	HasBody      bool
	Raw          bool
}

// Execute is HttpRequest.execute() (num_retries=0): the deserialized
// answer, or an HTTPError / transport / refresh exception.
func (s *Session) Execute(ctx context.Context, call Call) (any, error) {
	query := append([]Param(nil), call.Query...)
	if !call.Raw {
		query = append(query, Param{"alt", "json"})
	}
	uri := APIHost + call.Path
	if len(query) > 0 {
		pairs := make([][2]string, len(query))
		for i, param := range query {
			pairs[i] = [2]string{param.Name, param.Value}
		}
		uri += "?" + urlencode(pairs)
	}
	var body []byte
	accept := "application/json"
	if call.Raw {
		accept = "*/*"
	}
	headers := []Header{{"accept", accept}, {"accept-encoding", "gzip, deflate"}, {"user-agent", "(gzip)"},
		{"x-goog-api-client", apiClientHeader}}
	if call.HasBody {
		body = dto.PythonJSONDumps(call.Body)
		headers = append(headers, Header{"content-type", "application/json"})
	}
	headers = append(headers, Header{"content-length", strconv.Itoa(len(body))})
	var answer *Response
	for attempt := 0; ; attempt++ {
		if !s.credentials.Valid(s.now()) {
			if err := s.credentials.Refresh(ctx, s.transport, viaHTTPLib2, s.now); err != nil {
				return nil, err
			}
		}
		sent := append(append([]Header(nil), headers...), Header{"authorization", "Bearer " + s.credentials.Token.(string)})
		if quota, _ := s.credentials.Quota.(string); quota != "" {
			sent = append(sent, Header{"x-goog-user-project", quota})
		}
		var err error
		if answer, err = s.transport.Do(ctx, call.Method, uri, sent, body); err != nil {
			return nil, err
		}
		if answer.Status != 401 || attempt >= 2 {
			break
		}
		if err := s.credentials.Refresh(ctx, s.transport, viaHTTPLib2, s.now); err != nil {
			return nil, err
		}
	}
	if answer.Status >= 300 && answer.Status < 400 && call.Method == "GET" {
		// httplib2 follows redirects for GET: not ported.
		return nil, ErrNotPorted
	}
	if answer.Status >= 300 {
		return nil, &HTTPError{Status: answer.Status, Reason: answer.Reason, Body: answer.Body, URI: uri}
	}
	if answer.Status == 204 {
		if call.Raw {
			return "", nil
		}
		return domain.RequestObject{}, nil
	}
	if call.Raw {
		return string(answer.Body), nil
	}
	data, err := parseJSON(answer.Body)
	if isDecodeError(err) {
		return nil, err
	}
	if err != nil {
		return string(answer.Body), nil
	}
	return data, nil
}

// Revoke is requests.post(RevokeURI, params={'token': token},
// headers={'content-type': 'application/x-www-form-urlencoded'}); the answer is ignored.
func Revoke(ctx context.Context, transport *Transport, token any) error {
	target := RevokeURI
	if text, ok := token.(string); ok {
		target += "?" + urlencode([][2]string{{"token", text}})
	} else if token != nil {
		return ErrNotPorted
	}
	headers := requestsHeaders("*/*", []Header{{"content-type", "application/x-www-form-urlencoded"}}, 0)
	_, err := transport.Do(ctx, "POST", target, headers, nil)
	return err
}

// RefreshForLoad is get_google_credentials' credentials.refresh(Request()) (requests transport).
func RefreshForLoad(ctx context.Context, transport *Transport, credentials *Credentials, now func() time.Time) error {
	return credentials.Refresh(ctx, transport, viaRequests, now)
}

// PathSegment is a path parameter as googleapiclient expands it (quote(value, safe=”)).
func PathSegment(value string) string { return quote(value) }
