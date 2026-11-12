// Package stripeapi is the part of the stripe-python 11.6.0 SDK the Django
// views use: PaymentIntent retrieve/create against api_base with the SDK's
// exact form encoding, headers, retries and per-request telemetry, and
// stripe.Webhook.construct_event's signature verification.
package stripeapi

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"math/rand/v2"
	"net"
	"net/http"
	"net/url"
	"os"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handoff"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"github.com/google/uuid"
)

const (
	// DefaultAPIBase is stripe.api_base.
	DefaultAPIBase = "https://api.stripe.com"
	// Version is stripe.VERSION; APIVersion is stripe.api_version.
	Version    = "11.6.0"
	APIVersion = "2025-02-24.acacia"
	// maxNetworkRetries is stripe.max_network_retries.
	maxNetworkRetries = 2
	initialDelay      = 500 * time.Millisecond
	maxDelay          = 5 * time.Second
	maxRetryAfter     = 60
	// requestTimeout is RequestsClient's default timeout (80 s).
	requestTimeout = 80 * time.Second
)

// ErrNotPorted refuses a parameter value the SDK would encode in a way this
// port does not reproduce (a Float outside its fixed-notation repr range).
var ErrNotPorted = refusal.New("stripe parameter value not ported")

// Error is an SDK exception raised for a request. InvalidRequest is
// stripe.error.InvalidRequestError (the only class the views catch); every
// other failure (APIError, CardError, IdempotencyError, connection errors,
// invalid response bodies) is unhandled in the views.
type Error struct {
	Status         int
	InvalidRequest bool
	Message        string
}

func (e *Error) Error() string { return fmt.Sprintf("stripe: HTTP %d: %s", e.Status, e.Message) }

// Client sends requests to one API base. Sleep is replaceable in tests.
type Client struct {
	base  string
	http  *http.Client
	sleep func(context.Context, time.Duration) error
	agent string
}

func NewClient(base string) *Client {
	if base == "" {
		base = DefaultAPIBase
	}
	return &Client{base: strings.TrimRight(base, "/"), http: &http.Client{Timeout: requestTimeout}, sleep: sleepContext, agent: clientUserAgent()}
}

func sleepContext(ctx context.Context, d time.Duration) error {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

// clientUserAgent is X-Stripe-Client-User-Agent's json.dumps(ua). The first
// four values are the SDK's constants; lang_version, platform and uname
// describe the running process, as the SDK's do.
func clientUserAgent() string {
	host, _ := os.Hostname()
	ua := domain.RequestObject{
		{Name: "bindings_version", Value: Version}, {Name: "lang", Value: "python"}, {Name: "publisher", Value: "stripe"},
		{Name: "httplib", Value: "requests"}, {Name: "lang_version", Value: runtime.Version()},
		{Name: "platform", Value: runtime.GOOS + "-" + runtime.GOARCH},
		{Name: "uname", Value: strings.Join([]string{runtime.GOOS, host, runtime.GOARCH}, " ")},
	}
	return string(dto.PythonJSONDumps(ua))
}

// Session is one Django request's view of the SDK: Django serves each request
// on its own thread, so the HTTP client's thread-local last request metrics
// (X-Stripe-Client-Telemetry) only carry between calls of the same request.
type Session struct {
	client *Client
	key    string
	mu     sync.Mutex
	last   *metrics
}

type metrics struct {
	id       string
	duration int64
}

// Session starts the SDK state of one HTTP request with the request's API key.
func (c *Client) Session(apiKey string) *Session { return &Session{client: c, key: apiKey} }

// Param is one top-level parameter in the SDK's keyword order.
type Param struct {
	Key   string
	Value any
}

// Encode is stripe._encode._api_encode followed by urlencode and the
// SDK's '%5B'/'%5D' -> '['/']' replacement. Values are parsed JSON values
// (string, json.Number, bool, nil, []any, domain.RequestObject) or int64 and
// string conversions; a top-level None is omitted. A list nested in a list is
// yielded as-is and urlencode writes str(list) (dto.PythonStr); scalars are
// str() of the Python value.
func Encode(params []Param) (string, error) {
	var pairs []string
	var walk func(key string, value any, top bool) error
	walk = func(key string, value any, top bool) error {
		switch v := value.(type) {
		case nil:
			if top {
				return nil
			}
			pairs = append(pairs, quote(key)+"="+quote("None"))
		case []any:
			if !top {
				// _api_encode yields a list item that is not a dict as-is:
				// urlencode writes str(list), Python's repr.
				pairs = append(pairs, quote(key)+"="+quote(dto.PythonStr(v)))
				return nil
			}
			for index, item := range v {
				child := key + "[" + strconv.Itoa(index) + "]"
				if object, ok := item.(domain.RequestObject); ok {
					if err := walkObject(child, object, walk); err != nil {
						return err
					}
					continue
				}
				if err := walk(child, item, false); err != nil {
					return err
				}
			}
		case domain.RequestObject:
			return walkObject(key, v, walk)
		default:
			text, err := scalar(v)
			if err != nil {
				return err
			}
			pairs = append(pairs, quote(key)+"="+quote(text))
		}
		return nil
	}
	for _, param := range params {
		if err := walk(param.Key, param.Value, true); err != nil {
			return "", err
		}
	}
	return strings.NewReplacer("%5B", "[", "%5D", "]").Replace(strings.Join(pairs, "&")), nil
}

// walkObject is _encode_nested_dict: every member is re-encoded as a top-level value.
func walkObject(key string, object domain.RequestObject, walk func(string, any, bool) error) error {
	for _, field := range object {
		if err := walk(key+"["+field.Name+"]", field.Value, true); err != nil {
			return err
		}
	}
	return nil
}

// scalar is str(value) for the values urlencode receives.
func scalar(value any) (string, error) {
	switch v := value.(type) {
	case string:
		return v, nil
	case bool:
		if v {
			return "True", nil
		}
		return "False", nil
	case int64:
		return strconv.FormatInt(v, 10), nil
	case Float:
		return v.python()
	case json.Number:
		// str() of the parsed Python value: an int's digits (unbounded) or
		// str(float).
		return dto.PythonStr(v), nil
	}
	return "", ErrNotPorted
}

// Float is a Python float parameter (str(float) in the form body). Only the
// fixed-notation range of Python's repr (1e-4 <= |v| < 1e16, or 0) is ported.
type Float float64

func (f Float) python() (string, error) {
	v := float64(f)
	abs := math.Abs(v)
	if math.IsNaN(v) || math.IsInf(v, 0) || (abs != 0 && (abs < 1e-4 || abs >= 1e16)) {
		return "", ErrNotPorted
	}
	text := strconv.FormatFloat(v, 'f', -1, 64)
	if !strings.Contains(text, ".") {
		text += ".0"
	}
	return text, nil
}

// quote is urllib.parse.quote_plus (safe=”): Go's query escaping keeps the same
// unreserved set (letters, digits, '-', '_', '.', '~') and writes ' ' as '+'.
func quote(text string) string { return url.QueryEscape(text) }

// Object is a decoded API resource (key order preserved, Python JSON semantics).
type Object domain.RequestObject

// Lookup is attribute access: present reports whether the key exists.
func (o Object) Lookup(name string) (any, bool) { return domain.RequestObject(o).Lookup(name) }

// RetrievePaymentIntent is stripe.PaymentIntent.retrieve(id, api_key=...):
// GET /v1/payment_intents/<quote_plus(id)>.
func (s *Session) RetrievePaymentIntent(ctx context.Context, id string) (Object, error) {
	return s.request(ctx, http.MethodGet, "/v1/payment_intents/"+quote(id), "")
}

// CreatePaymentIntent is stripe.PaymentIntent.create(api_key=..., **params).
func (s *Session) CreatePaymentIntent(ctx context.Context, params []Param) (Object, error) {
	body, err := Encode(params)
	if err != nil {
		return nil, err
	}
	return s.request(ctx, http.MethodPost, "/v1/payment_intents", body)
}

// List is <Resource>.list(api_key=..., **params) and ListObject.next_page:
// GET <path>?<params> (the query keeps urlencode's %5B/%5D; only form bodies
// get the bracket replacement).
func (s *Session) List(ctx context.Context, path string, params []Param) (Object, error) {
	query, err := Encode(params)
	if err != nil {
		return nil, err
	}
	if query != "" {
		path += "?" + strings.NewReplacer("[", "%5B", "]", "%5D").Replace(query)
	}
	return s.request(ctx, http.MethodGet, path, "")
}

// Create is <Resource>.create(api_key=..., **params): POST <path> with the
// encoded parameters (stripe.Coupon.create: /v1/coupons,
// stripe.PromotionCode.create: /v1/promotion_codes).
func (s *Session) Create(ctx context.Context, path string, params []Param) (Object, error) {
	body, err := Encode(params)
	if err != nil {
		return nil, err
	}
	return s.request(ctx, http.MethodPost, path, body)
}

func (s *Session) telemetry() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.last == nil {
		return ""
	}
	payload := domain.RequestObject{{Name: "last_request_metrics", Value: domain.RequestObject{
		{Name: "request_id", Value: s.last.id}, {Name: "request_duration_ms", Value: json.Number(strconv.FormatInt(s.last.duration, 10))}}}}
	return string(dto.PythonJSONDumps(payload))
}

// request is _APIRequestor.request_raw + HTTPClient._request_with_retries_internal
// + _interpret_response for api mode V1.
func (s *Session) request(ctx context.Context, method, path, body string) (Object, error) {
	headers := http.Header{}
	headers.Set("X-Stripe-Client-User-Agent", s.client.agent)
	headers.Set("User-Agent", "Stripe/v1 PythonBindings/"+Version)
	headers.Set("Authorization", "Bearer "+s.key)
	if method == http.MethodPost {
		headers.Set("Idempotency-Key", uuid.NewString())
		headers.Set("Content-Type", "application/x-www-form-urlencoded")
	}
	headers.Set("Stripe-Version", APIVersion)
	// The telemetry header is computed once, before the first attempt.
	if telemetry := s.telemetry(); telemetry != "" {
		headers.Set("X-Stripe-Client-Telemetry", telemetry)
	}
	retries := 0
	for {
		start := nowMillis()
		status, data, responseHeaders, err := s.client.send(ctx, method, s.client.base+path, headers, body)
		if s.retry(status, responseHeaders, err, retries) {
			retries++
			if sleepErr := s.client.sleep(ctx, sleepTime(retries, responseHeaders)); sleepErr != nil {
				return nil, sleepErr
			}
			continue
		}
		if err != nil {
			return nil, &Error{Message: "connection error"}
		}
		if values, present := responseHeaders["Request-Id"]; present {
			id := ""
			if len(values) > 0 {
				id = strings.Join(values, ", ")
			}
			s.mu.Lock()
			s.last = &metrics{id: id, duration: nowMillis() - start}
			s.mu.Unlock()
		}
		return interpret(status, data)
	}
}

// nowMillis is stripe._http_client._now_ms: int(round(time.time() * 1000)).
func nowMillis() int64 { return int64(math.RoundToEven(float64(time.Now().UnixNano()) / 1e6)) }

type connectionError struct {
	err   error
	retry bool
}

func (e *connectionError) Error() string { return e.err.Error() }

func (c *Client) send(ctx context.Context, method, target string, headers http.Header, body string) (int, []byte, http.Header, error) {
	var reader io.Reader
	if method == http.MethodPost {
		reader = bytes.NewReader([]byte(body))
		handoff.MarkEffect(ctx, "stripe request")
	}
	request, err := http.NewRequestWithContext(ctx, method, target, reader)
	if err != nil {
		return 0, nil, nil, &connectionError{err: err}
	}
	request.Header = headers.Clone()
	response, err := c.http.Do(request)
	if err != nil {
		// requests' Timeout and ConnectionError are retried; others are not.
		var netErr net.Error
		retry := errors.As(err, &netErr) || errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF)
		return 0, nil, nil, &connectionError{err: err, retry: retry && ctx.Err() == nil}
	}
	defer response.Body.Close()
	data, err := io.ReadAll(response.Body)
	if err != nil {
		return 0, nil, nil, &connectionError{err: err, retry: true}
	}
	return response.StatusCode, data, response.Header, nil
}

// retry is HTTPClient._should_retry with max_network_retries = 2.
func (s *Session) retry(status int, headers http.Header, err error, retries int) bool {
	if retries >= maxNetworkRetries {
		return false
	}
	if err != nil {
		var connection *connectionError
		return errors.As(err, &connection) && connection.retry
	}
	if values, present := headers["Stripe-Should-Retry"]; present && len(values) > 0 {
		switch strings.Join(values, ", ") {
		case "false":
			return false
		case "true":
			return true
		}
	}
	return status == http.StatusConflict || status >= 500
}

// sleepTime is HTTPClient._sleep_time_seconds: exponential backoff with
// jitter, at least the initial delay, and a reasonable Retry-After.
func sleepTime(retries int, headers http.Header) time.Duration {
	delay := math.Min(float64(initialDelay)*math.Pow(2, float64(retries-1)), float64(maxDelay))
	delay *= 0.5 * (1 + rand.Float64())
	delay = math.Max(float64(initialDelay), delay)
	if headers != nil {
		if after, err := strconv.Atoi(strings.TrimSpace(headers.Get("Retry-After"))); err == nil && after <= maxRetryAfter {
			delay = math.Max(float64(after)*float64(time.Second), delay)
		}
	}
	return time.Duration(delay)
}

// interpret is _interpret_response: the body is decoded (JSON failure:
// APIError), then any status outside 2xx is an error classified like
// specific_v1_api_error; a success that is not a JSON object cannot be used
// as a resource.
func interpret(status int, data []byte) (Object, error) {
	if !utf8Valid(data) {
		return nil, &Error{Status: status, Message: "invalid response body"}
	}
	parsed, err := dto.ParseProfileTablesJSON(data)
	if err != nil {
		return nil, &Error{Status: status, Message: "invalid response body"}
	}
	object, isObject := parsed.(domain.RequestObject)
	if status < 200 || status >= 300 {
		failure := &Error{Status: status, Message: "API error"}
		if !isObject {
			return nil, failure
		}
		detail, present := object.Lookup("error")
		fields, ok := detail.(domain.RequestObject)
		if !present || !ok {
			return nil, failure
		}
		kind, _ := fields.Lookup("type")
		code, _ := fields.Lookup("code")
		rateLimited := status == 429 || (status == 400 && code == "rate_limit")
		failure.InvalidRequest = !rateLimited && (status == 400 || status == 404) && kind != "idempotency_error"
		return nil, failure
	}
	if !isObject {
		return nil, &Error{Status: status, Message: "response is not an object"}
	}
	return Object(object), nil
}

func utf8Valid(data []byte) bool { return strings.ToValidUTF8(string(data), "�") == string(data) }
