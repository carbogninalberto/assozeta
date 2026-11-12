// Package agentllm is the OpenAI-compatible chat completions client the
// Django agent uses: AsyncOpenAI 2.54.0 (openai>=2,<3 in BE/requirements,
// the version the parity runs pin) over httpx 0.28.1. It reproduces the
// request (URL, headers, the compact JSON body httpx writes), the SDK's own
// retries (max_retries=2, x-should-retry, 408/409/429/5xx, connection errors,
// retry-after-ms/retry-after, exponential backoff with jitter) and the SSE
// stream decoding the SDK applies before ChatCompletionChunk objects are built.
package agentllm

import (
	"bufio"
	"bytes"
	"compress/flate"
	"compress/gzip"
	"context"
	"errors"
	"io"
	"math"
	"math/rand"
	"net"
	"net/http"
	"net/mail"
	"net/url"
	"os"
	"runtime"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handoff"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// SDKVersion is the openai distribution version the requests name.
const SDKVersion = "2.54.0"

// RuntimeVersion is platform.python_version() of the production interpreter
// (python:3.13 image, the version the parity runs pin).
const RuntimeVersion = "3.13.16"

const (
	maxRetries          = 2
	initialRetryDelay   = 0.5
	maxRetryDelay       = 8.0
	maxRetryAfterDelay  = 120.0
	defaultBaseURL      = "https://api.openai.com/v1"
	readTimeout         = 600 * time.Second
	connectTimeout      = 5 * time.Second
	maxErrorBody        = 1 << 20
	maxStreamLine       = 16 << 20
	streamContentHeader = "x-stainless-read-timeout"
)

// ErrNotPorted refuses an input whose SDK behaviour this client does not
// reproduce (a base URL httpx would rewrite or reject).
var ErrNotPorted = refusal.New("agent model endpoint input not ported")

// APIError is an exception the SDK raises from a request or a stream; Text
// is str(exception), which the agent inspects for its own retry decision.
type APIError struct{ Text string }

func (e *APIError) Error() string { return e.Text }

// Config is AsyncOpenAI(api_key=..., base_url=...): BaseURL empty means the
// argument was not passed (OPENAI_BASE_URL, then the SDK default).
type Config struct {
	APIKey  string
	BaseURL string
	// Getenv reads the process environment (OPENAI_BASE_URL, OPENAI_ORG_ID,
	// OPENAI_PROJECT_ID) like the SDK constructor; nil means os.LookupEnv.
	Getenv func(string) (string, bool)
	// Transport replaces the HTTP transport (tests); nil uses a proxy-aware one.
	Transport http.RoundTripper
}

// Client is one AsyncOpenAI instance.
type Client struct {
	base    *url.URL
	headers [][2]string
	http    *http.Client
	// Sleep waits between retries; Random is random.random().
	Sleep  func(context.Context, time.Duration) error
	Random func() float64
}

// New validates the configuration like AsyncOpenAI(...) and httpx.URL.
func New(cfg Config) (*Client, error) {
	getenv := cfg.Getenv
	if getenv == nil {
		getenv = os.LookupEnv
	}
	raw := cfg.BaseURL
	if raw == "" {
		if value, ok := getenv("OPENAI_BASE_URL"); ok && value != "" {
			raw = value
		} else {
			raw = defaultBaseURL
		}
	}
	base, err := baseURL(raw)
	if err != nil {
		return nil, err
	}
	headers := [][2]string{{"Accept", "application/json"}, {"Content-Type", "application/json"},
		{"User-Agent", "AsyncOpenAI/Python " + SDKVersion}, {"X-Stainless-Lang", "python"},
		{"X-Stainless-Package-Version", SDKVersion}, {"X-Stainless-OS", platformName()},
		{"X-Stainless-Arch", architecture()}, {"X-Stainless-Runtime", "CPython"},
		{"X-Stainless-Runtime-Version", RuntimeVersion}, {"X-Stainless-Async", "async:asyncio"}}
	if value, ok := getenv("OPENAI_ORG_ID"); ok {
		headers = append(headers, [2]string{"OpenAI-Organization", value})
	}
	if value, ok := getenv("OPENAI_PROJECT_ID"); ok {
		headers = append(headers, [2]string{"OpenAI-Project", value})
	}
	headers = append(headers, [2]string{"Authorization", "Bearer " + cfg.APIKey})
	transport := cfg.Transport
	if transport == nil {
		transport = &http.Transport{
			Proxy:                 http.ProxyFromEnvironment,
			DialContext:           (&net.Dialer{Timeout: connectTimeout, KeepAlive: 30 * time.Second}).DialContext,
			TLSHandshakeTimeout:   connectTimeout,
			ResponseHeaderTimeout: readTimeout,
			MaxIdleConnsPerHost:   100,
			IdleConnTimeout:       5 * time.Second,
			DisableCompression:    true,
		}
	}
	client := &http.Client{Transport: transport, CheckRedirect: func(req *http.Request, via []*http.Request) error {
		if len(via) >= 20 {
			return errors.New("too many redirects")
		}
		return nil
	}}
	return &Client{base: base, headers: headers, http: client, Sleep: sleep, Random: rand.Float64}, nil
}

// baseURL is the SDK's base URL with its enforced trailing slash. Only plain
// http(s) URLs that httpx keeps as written are ported.
func baseURL(raw string) (*url.URL, error) {
	for _, r := range raw {
		if r <= ' ' || r >= 0x7f || strings.ContainsRune(`"<>\^{|}`+"`", r) {
			return nil, ErrNotPorted
		}
	}
	parsed, err := url.Parse(raw)
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Host == "" || parsed.User != nil ||
		parsed.RawQuery != "" || parsed.Fragment != "" || parsed.Opaque != "" || strings.Contains(raw, "%") ||
		parsed.Host != strings.ToLower(parsed.Host) || strings.HasSuffix(parsed.Host, ":") {
		return nil, ErrNotPorted
	}
	if !strings.HasSuffix(parsed.Path, "/") {
		parsed.Path += "/"
	}
	if parsed.Path == "" {
		parsed.Path = "/"
	}
	return parsed, nil
}

// platformName is openai._base_client.get_platform() for the platforms Go runs on.
func platformName() string {
	switch runtime.GOOS {
	case "darwin":
		return "MacOS"
	case "linux":
		return "Linux"
	case "windows":
		return "Windows"
	case "freebsd":
		return "FreeBSD"
	case "openbsd":
		return "OpenBSD"
	case "android":
		return "Android"
	case "ios":
		return "iOS"
	}
	return "Other:" + runtime.GOOS
}

// architecture is get_architecture(): platform.machine() lower-cased, mapped.
func architecture() string {
	switch runtime.GOARCH {
	case "arm64":
		return "arm64"
	case "amd64":
		return "x64"
	case "386":
		return "x32"
	case "arm":
		return "arm"
	}
	return "other:" + runtime.GOARCH
}

func sleep(ctx context.Context, d time.Duration) error {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

// Stream is the body of a successful streamed completion.
type Stream struct {
	body    io.ReadCloser
	reader  *bufio.Reader
	decoder sseDecoder
	pending []Event
	done    bool
}

// Close releases the response.
func (s *Stream) Close() error { return s.body.Close() }

// Event is one server-sent event.
type Event struct {
	Event *string
	Data  string
}

// Create sends POST {base}chat/completions with the SDK's retries. A non-nil
// *APIError is the exception the SDK raises; other errors are the context's.
func (c *Client) Create(ctx context.Context, body []byte) (*Stream, error) {
	target := *c.base
	target.Path += "chat/completions"
	handoff.MarkEffect(ctx, "model request")
	for retries := 0; ; retries++ {
		remaining := maxRetries - retries
		request, err := http.NewRequestWithContext(ctx, http.MethodPost, target.String(), bytes.NewReader(body))
		if err != nil {
			return nil, err
		}
		for _, header := range c.headers {
			request.Header[header[0]] = []string{header[1]}
		}
		request.Header["Accept-Encoding"] = []string{"gzip, deflate"}
		request.Header["Connection"] = []string{"keep-alive"}
		request.Header["x-stainless-retry-count"] = []string{strconv.Itoa(retries)}
		request.Header[streamContentHeader] = []string{"600"}
		response, err := c.http.Do(request)
		if err != nil {
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			text := "Connection error."
			var timeout interface{ Timeout() bool }
			if errors.As(err, &timeout) && timeout.Timeout() {
				text = "Request timed out."
			}
			if remaining > 0 {
				if err := c.wait(ctx, retries, nil); err != nil {
					return nil, err
				}
				continue
			}
			return nil, &APIError{Text: text}
		}
		if response.StatusCode >= 200 && response.StatusCode < 300 {
			reader, err := decoded(response)
			if err != nil {
				_ = response.Body.Close()
				return nil, &APIError{Text: "Connection error."}
			}
			return &Stream{body: response.Body, reader: bufio.NewReaderSize(reader, 64<<10)}, nil
		}
		if remaining > 0 && shouldRetry(response.StatusCode, response.Header) {
			_ = response.Body.Close()
			if err := c.wait(ctx, retries, response.Header); err != nil {
				return nil, err
			}
			continue
		}
		text, err := statusErrorText(response)
		_ = response.Body.Close()
		if err != nil {
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			return nil, &APIError{Text: "Connection error."}
		}
		return nil, &APIError{Text: text}
	}
}

func decoded(response *http.Response) (io.Reader, error) {
	var reader io.Reader = response.Body
	encodings := strings.Split(response.Header.Get("Content-Encoding"), ",")
	for i := len(encodings) - 1; i >= 0; i-- {
		switch strings.TrimSpace(strings.ToLower(encodings[i])) {
		case "gzip", "x-gzip":
			gz, err := gzip.NewReader(reader)
			if err != nil {
				return nil, err
			}
			reader = gz
		case "deflate":
			reader = flate.NewReader(reader)
		}
	}
	return reader, nil
}

func (c *Client) wait(ctx context.Context, retries int, headers http.Header) error {
	seconds := c.retryTimeout(retries, headers)
	return c.Sleep(ctx, time.Duration(seconds*float64(time.Second)))
}

// retryTimeout is _calculate_retry_timeout(max_retries - retries, ...).
func (c *Client) retryTimeout(retries int, headers http.Header) float64 {
	if after, ok := retryAfter(headers); ok && !math.IsInf(after, 0) && !math.IsNaN(after) && after > 0 && after <= maxRetryAfterDelay {
		return after
	}
	delay := math.Min(initialRetryDelay*math.Pow(2, float64(retries)), maxRetryDelay)
	timeout := delay * (1 - 0.25*c.Random())
	if timeout < 0 {
		return 0
	}
	return timeout
}

// shouldRetry is _should_retry for an error response.
func shouldRetry(status int, headers http.Header) bool {
	if after, ok := retryAfter(headers); ok && !math.IsInf(after, 0) && !math.IsNaN(after) && after > maxRetryAfterDelay {
		return false
	}
	switch headers.Get("x-should-retry") {
	case "true":
		return true
	case "false":
		return false
	}
	return status == 408 || status == 409 || status == 429 || status >= 500
}

// retryAfter is _parse_retry_after_header.
func retryAfter(headers http.Header) (float64, bool) {
	if headers == nil {
		return 0, false
	}
	if values, ok := headers["Retry-After-Ms"]; ok && len(values) > 0 {
		if value, ok := pythonFloat(joinHeader(values)); ok {
			return value / 1000, true
		}
	}
	values, ok := headers["Retry-After"]
	if !ok || len(values) == 0 {
		return 0, false
	}
	text := joinHeader(values)
	if value, ok := pythonFloat(text); ok {
		return value, true
	}
	when, err := mail.ParseDate(text)
	if err != nil {
		return 0, false
	}
	return float64(when.Unix()) - float64(time.Now().UnixNano())/1e9, true
}

// joinHeader is httpx.Headers.get for a repeated header: the values joined.
func joinHeader(values []string) string { return strings.Join(values, ", ") }

// pythonFloat is float(text) for the forms headers carry.
func pythonFloat(text string) (float64, bool) {
	text = strings.TrimSpace(text)
	switch strings.ToLower(strings.TrimLeft(text, "+-")) {
	case "inf", "infinity", "nan":
		value, err := strconv.ParseFloat(text, 64)
		return value, err == nil
	}
	if text == "" || strings.ContainsAny(text, "_xXpP") {
		return 0, false
	}
	value, err := strconv.ParseFloat(text, 64)
	if err != nil {
		var numErr *strconv.NumError
		if errors.As(err, &numErr) && errors.Is(numErr.Err, strconv.ErrRange) {
			return value, true
		}
		return 0, false
	}
	return value, true
}

// statusErrorText is str(_make_status_error_from_response(response)).
func statusErrorText(response *http.Response) (string, error) {
	reader, err := decoded(response)
	if err != nil {
		return "", err
	}
	data, err := io.ReadAll(io.LimitReader(reader, maxErrorBody))
	if err != nil {
		return "", err
	}
	text := strings.TrimFunc(responseText(data, response.Header.Get("Content-Type")), pythonSpace)
	if parsed, ok := parseJSON(text); ok {
		return "Error code: " + strconv.Itoa(response.StatusCode) + " - " + pythonStr(parsed), nil
	}
	if text != "" {
		return text, nil
	}
	return "Error code: " + strconv.Itoa(response.StatusCode), nil
}

// responseText is httpx's Response.text: the declared charset when it is
// UTF-8 compatible, otherwise UTF-8 with replacement.
func responseText(data []byte, contentType string) string {
	if utf8.Valid(data) {
		return string(data)
	}
	return strings.ToValidUTF8(string(data), "�")
}

func pythonSpace(r rune) bool {
	switch r {
	case ' ', '\t', '\n', '\v', '\f', '\r', 0x1c, 0x1d, 0x1e, 0x1f, 0x85, 0xa0, 0x1680, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000:
		return true
	}
	return r >= 0x2000 && r <= 0x200a
}

// Next returns the next event the SDK's SSEDecoder yields; io.EOF at the end.
func (s *Stream) Next() (Event, error) {
	for {
		if len(s.pending) > 0 {
			event := s.pending[0]
			s.pending = s.pending[1:]
			return event, nil
		}
		if s.done {
			return Event{}, io.EOF
		}
		chunk, err := s.readChunk()
		if err != nil && !errors.Is(err, io.EOF) {
			return Event{}, &APIError{Text: "Connection error."}
		}
		if errors.Is(err, io.EOF) {
			s.done = true
		}
		if len(chunk) > 0 {
			events, decodeErr := s.decoder.feed(chunk)
			if decodeErr != nil {
				return Event{}, decodeErr
			}
			s.pending = append(s.pending, events...)
		}
	}
}

// readChunk is _aiter_chunks: lines (splitlines(keepends=True)) accumulated
// until the data ends with a blank line.
func (s *Stream) readChunk() ([]byte, error) {
	var data []byte
	for {
		line, err := readLine(s.reader)
		data = append(data, line...)
		if len(data) > maxStreamLine {
			return nil, errors.New("stream line too long")
		}
		if bytes.HasSuffix(data, []byte("\r\r")) || bytes.HasSuffix(data, []byte("\n\n")) || bytes.HasSuffix(data, []byte("\r\n\r\n")) {
			return data, err
		}
		if err != nil {
			return data, err
		}
	}
}

// readLine reads one line ending in \n, \r or \r\n (bytes.splitlines).
func readLine(reader *bufio.Reader) ([]byte, error) {
	var line []byte
	for {
		b, err := reader.ReadByte()
		if err != nil {
			return line, err
		}
		line = append(line, b)
		if b == '\n' {
			return line, nil
		}
		if b == '\r' {
			next, err := reader.Peek(1)
			if err == nil && next[0] == '\n' {
				_, _ = reader.ReadByte()
				line = append(line, '\n')
			}
			return line, nil
		}
	}
}

type sseDecoder struct {
	event       *string
	data        []string
	lastEventID *string
	retry       *int
}

// feed is SSEDecoder.aiter_bytes for one chunk: splitlines, UTF-8 decode, decode().
func (d *sseDecoder) feed(chunk []byte) ([]Event, error) {
	var events []Event
	for _, raw := range splitLines(chunk) {
		if !utf8.Valid(raw) {
			return nil, &APIError{Text: "'utf-8' codec can't decode bytes"}
		}
		if event, ok := d.decode(string(raw)); ok {
			events = append(events, event)
		}
	}
	return events, nil
}

func splitLines(data []byte) [][]byte {
	var lines [][]byte
	start := 0
	for i := 0; i < len(data); i++ {
		switch data[i] {
		case '\n':
			lines = append(lines, data[start:i])
			start = i + 1
		case '\r':
			lines = append(lines, data[start:i])
			if i+1 < len(data) && data[i+1] == '\n' {
				i++
			}
			start = i + 1
		}
	}
	if start < len(data) {
		lines = append(lines, data[start:])
	}
	return lines
}

func (d *sseDecoder) decode(line string) (Event, bool) {
	if line == "" {
		if d.event == nil && len(d.data) == 0 && d.lastEventID == nil && d.retry == nil {
			return Event{}, false
		}
		event := Event{Event: d.event, Data: strings.Join(d.data, "\n")}
		d.event, d.data, d.retry = nil, nil, nil
		return event, true
	}
	if strings.HasPrefix(line, ":") {
		return Event{}, false
	}
	field, value, _ := strings.Cut(line, ":")
	value = strings.TrimPrefix(value, " ")
	switch field {
	case "event":
		d.event = &value
	case "data":
		d.data = append(d.data, value)
	case "id":
		if !strings.Contains(value, "\x00") {
			d.lastEventID = &value
		}
	case "retry":
		if number, err := strconv.Atoi(strings.TrimSpace(value)); err == nil && strings.TrimSpace(value) == value {
			d.retry = &number
		}
	}
	return Event{}, false
}

func parseJSON(text string) (any, bool) {
	value, err := dto.ParsePythonJSON(text)
	return value, err == nil
}

func pythonStr(value any) string { return dto.PythonStr(value) }
