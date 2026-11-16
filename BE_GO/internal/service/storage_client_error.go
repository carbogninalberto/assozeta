package service

import (
	"context"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"syscall"

	"github.com/aws/aws-sdk-go-v2/aws/retry"
	"github.com/aws/smithy-go"
	smithyhttp "github.com/aws/smithy-go/transport/http"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentobjects"
)

// botocoreMaxRetries is the retry count botocore's legacy retry mode reports
// in its ClientError once it gives up: its default configuration makes
// max_attempts 5 requests, 4 of them retries.
const botocoreMaxRetries = 4

// botocoreRetriedCodes are the HTTP 400 error codes botocore's legacy retry
// mode retries: the default configuration's throttling policies and S3's own
// RequestTimeout and BadDigest policies (botocore/data/_retry.json).
var botocoreRetriedCodes = map[string]bool{"RequestTimeout": true, "Throttling": true,
	"ThrottlingException": true, "ThrottledException": true, "RequestThrottledException": true,
	"ProvisionedThroughputExceededException": true, "BadDigest": true}

// botocoreRetriedStatuses are the HTTP answers botocore's legacy retry mode
// retries whatever the body (the 429 policy and the server-error policies).
var botocoreRetriedStatuses = map[int]bool{429: true, 500: true, 502: true, 503: true, 504: true, 509: true}

// botocoreHTTPPhrases holds http.client.responses for HTTPStatus 400-599: an
// answer without a parsed code (a HEAD answer has no body, a proxy may answer
// HTML) takes the code and the message from the status (empty for a status
// Python does not name, 509 among them).
var botocoreHTTPPhrases = map[int]string{
	400: "Bad Request", 401: "Unauthorized", 402: "Payment Required", 403: "Forbidden", 404: "Not Found",
	405: "Method Not Allowed", 406: "Not Acceptable", 407: "Proxy Authentication Required", 408: "Request Timeout",
	409: "Conflict", 410: "Gone", 411: "Length Required", 412: "Precondition Failed", 413: "Content Too Large",
	414: "URI Too Long", 415: "Unsupported Media Type", 416: "Range Not Satisfiable", 417: "Expectation Failed",
	418: "I'm a Teapot", 421: "Misdirected Request", 422: "Unprocessable Content", 423: "Locked",
	424: "Failed Dependency", 425: "Too Early", 426: "Upgrade Required", 428: "Precondition Required",
	429: "Too Many Requests", 431: "Request Header Fields Too Large", 451: "Unavailable For Legal Reasons",
	500: "Internal Server Error", 501: "Not Implemented", 502: "Bad Gateway", 503: "Service Unavailable",
	504: "Gateway Timeout", 505: "HTTP Version Not Supported", 506: "Variant Also Negotiates",
	507: "Insufficient Storage", 508: "Loop Detected", 510: "Not Extended", 511: "Network Authentication Required",
}

// botocoreHTTPPhrase is http.client.responses[status]: the phrase table above,
// then Go's (the same English phrases for the statuses Python names).
func botocoreHTTPPhrase(status int) string {
	if phrase, known := botocoreHTTPPhrases[status]; known {
		return phrase
	}
	return http.StatusText(status)
}

// botocoreClientErrorText is str() of the ClientError botocore raises for a
// storage error answer it does not retry: "An error occurred (<code>) when
// calling the <operation> operation: <message>". The operation is the one
// boto3 names, which for a multipart save differs from the SDK's request.
// ok=false when the error is not an HTTP answer.
func botocoreClientErrorText(err error, operation string) (string, bool) {
	var response *smithyhttp.ResponseError
	if !errors.As(err, &response) {
		return "", false
	}
	code, message := botocoreAnswerCodeMessage(err, response.HTTPStatusCode(), operation == "HeadObject")
	return "An error occurred (" + code + ") when calling the " + operation + " operation: " + message, true
}

// botocoreAnswerCodeMessage is the error code and message botocore parses from
// an answer: the parsed ones, or the HTTP status and its reason phrase when the
// answer carries no parseable code (a HEAD answer has no body, a proxy may
// answer HTML). The S3 deserializer derives a code and message from the status
// itself in that case (spaces removed, "UnknownError" for a status Go's
// http.StatusText does not name), which is how they are recognised.
func botocoreAnswerCodeMessage(err error, status int, head bool) (code, message string) {
	phrase := http.StatusText(status)
	syntheticCode := strings.ReplaceAll(phrase, " ", "")
	if phrase == "" {
		syntheticCode, phrase = "UnknownError", "UnknownError"
	}
	if !head {
		var api smithy.APIError
		if errors.As(err, &api) {
			code, message = api.ErrorCode(), api.ErrorMessage()
			if code != syntheticCode || message != phrase {
				if message == "UnknownError" || message == "" {
					// The deserializer's placeholder for a body without a
					// message: botocore's message is absent, so ClientError
					// reads its default.
					message = "Unknown"
				}
				return code, message
			}
		}
	}
	return strconv.Itoa(status), botocoreHTTPPhrase(status)
}

// botocoreFinalErrorText is str() of the exception botocore raises, after its
// retries, for a storage error the aws-sdk-go-v2 client returns: the ClientError
// of an error answer (with " (reached max retries: 4)" when botocore retries it
// or the SDK gave up) or one of the transport errors. ok=false for a cancelled
// request context, an answer without an error status, a closed connection
// whose request URL is unknown, and the SDK's own configuration errors: none of
// those is a botocore answer this port reproduces.
func botocoreFinalErrorText(err error, operation string) (string, bool) {
	return botocoreFinalErrorTextSuffixed(err, operation, "")
}

// botocoreFinalErrorTextSuffixed is botocoreFinalErrorText for a request whose
// URL carries a suffix botocore adds, such as a multipart upload's "?uploads".
func botocoreFinalErrorTextSuffixed(err error, operation, urlSuffix string) (string, bool) {
	var head *documentobjects.HeadStatusError
	if errors.As(err, &head) {
		// Exists already turned the HEAD client-error answer into botocore's text.
		return head.Error(), true
	}
	if errors.Is(err, context.Canceled) {
		// A cancelled request is not a storage answer.
		return "", false
	}
	if text, ok := botocoreTransportErrorText(err, urlSuffix); ok {
		return text, true
	}
	var maxAttempts *retry.MaxAttemptsError
	gaveUp := errors.As(err, &maxAttempts)
	var response *smithyhttp.ResponseError
	if !errors.As(err, &response) {
		return "", false
	}
	status := response.HTTPStatusCode()
	if status < 300 {
		// boto3 raises a ClientError for an error status only.
		return "", false
	}
	code, message := botocoreAnswerCodeMessage(err, status, operation == "HeadObject")
	if gaveUp || botocoreRetriedStatuses[status] || (status == http.StatusBadRequest && botocoreRetriedCodes[code]) {
		return "An error occurred (" + code + ") when calling the " + operation + " operation (reached max retries: " +
			strconv.Itoa(botocoreMaxRetries) + "): " + message, true
	}
	return botocoreClientErrorText(err, operation)
}

// botocoreTransportErrorText is the exception botocore raises when no HTTP
// answer arrived: the errors of botocore/httpsession.py. ok=false for an error
// with neither a request URL nor a closed connection (not a transport error)
// and for a closed connection whose endpoint cannot be named.
func botocoreTransportErrorText(err error, urlSuffix string) (string, bool) {
	var urlErr *url.Error
	hasURL := errors.As(err, &urlErr)
	closed := botocoreConnectionClosed(err)
	if !hasURL && !closed {
		return "", false
	}
	endpoint := ""
	if hasURL {
		endpoint = botocoreRequestURL(urlErr.URL, urlSuffix)
	} else {
		endpoint = botocoreResponseURL(err, urlSuffix)
	}
	if endpoint == "" {
		// No request URL to name: not a transport error botocore reports.
		return "", false
	}
	switch {
	case hasURL && botocoreConnectTimeout(err):
		return "Connect timeout on endpoint URL: \"" + endpoint + "\"", true
	case closed:
		return "Connection was closed before we received a valid response from endpoint URL: \"" + endpoint + "\".", true
	case botocoreReadTimeout(err, urlErr):
		return "Read timeout on endpoint URL: \"" + endpoint + "\"", true
	default:
		// Connection refused, DNS failure, unreachable network: botocore's
		// EndpointConnectionError.
		return "Could not connect to the endpoint URL: \"" + endpoint + "\"", true
	}
}

// botocoreConnectTimeout reports a connection that timed out while being
// opened: botocore's ConnectTimeoutError.
func botocoreConnectTimeout(err error) bool {
	var netErr *net.OpError
	return errors.As(err, &netErr) && netErr.Op == "dial" && netErr.Timeout()
}

// botocoreReadTimeout reports a request whose answer timed out after the
// connection was opened: botocore's ReadTimeoutError (urllib3's read timeout,
// Go's response-header or read deadline).
func botocoreReadTimeout(err error, urlErr *url.Error) bool {
	if urlErr != nil && urlErr.Timeout() {
		return true
	}
	if errors.Is(err, os.ErrDeadlineExceeded) {
		return true
	}
	var netErr net.Error
	return errors.As(err, &netErr) && netErr.Timeout()
}

// botocoreConnectionClosed reports a connection closed before a complete
// answer: botocore's ConnectionClosedError (urllib3's ProtocolError).
func botocoreConnectionClosed(err error) bool {
	if errors.Is(err, io.ErrUnexpectedEOF) || errors.Is(err, io.EOF) || errors.Is(err, net.ErrClosed) ||
		errors.Is(err, syscall.ECONNRESET) || errors.Is(err, syscall.EPIPE) {
		return true
	}
	message := err.Error()
	return strings.Contains(message, "connection reset by peer") ||
		strings.Contains(message, "use of closed network connection") ||
		strings.Contains(message, "server closed idle connection")
}

// botocoreRequestURL is the URL botocore names in a transport error: the one Go
// requested, without the x-id query parameter the S3 client adds, and with the
// suffix boto3's own request carries.
func botocoreRequestURL(requestURL, suffix string) string {
	parsed, err := url.Parse(requestURL)
	if err != nil {
		return requestURL + suffix
	}
	if query := parsed.Query(); query.Has("x-id") {
		query.Del("x-id")
		parsed.RawQuery = query.Encode()
	}
	result := parsed.String()
	switch {
	case suffix == "":
		return result
	case parsed.RawQuery == "":
		return result + suffix
	default:
		return result + "&" + strings.TrimPrefix(suffix, "?")
	}
}

// botocoreResponseURL is the request URL of an answer that was cut short after
// its status line, when no *url.Error carries it.
func botocoreResponseURL(err error, suffix string) string {
	var response *smithyhttp.ResponseError
	if !errors.As(err, &response) || response.Response == nil || response.Response.Response == nil {
		return ""
	}
	request := response.Response.Response.Request
	if request == nil || request.URL == nil {
		return ""
	}
	return botocoreRequestURL(request.URL.String(), suffix)
}

// documentExistsError is default_storage's exists check (HeadObject) failing
// before the save's own request: boto3 names HeadObject whatever the content
// size, so a multipart save's CreateMultipartUpload naming must not apply.
type documentExistsError struct{ err error }

func (e *documentExistsError) Error() string { return e.err.Error() }

func (e *documentExistsError) Unwrap() error { return e.err }

// documentSaveFailureText is the text of default_storage.save's exception for
// content of size bytes. Content of multipart_threshold bytes and up goes
// through s3transfer's multipart upload, whose first request is
// CreateMultipartUpload (with "?uploads" in a transport error's URL): boto3
// names that operation in the text. The exists check before the save is a
// single HeadObject, whatever the content size.
func documentSaveFailureText(err error, size int) (string, bool) {
	var exists *documentExistsError
	if errors.As(err, &exists) {
		return botocoreFinalErrorText(exists.err, "HeadObject")
	}
	if size >= consumerStorageMultipartThreshold {
		return botocoreFinalErrorTextSuffixed(err, "CreateMultipartUpload", "?uploads")
	}
	return botocoreFinalErrorText(err, "PutObject")
}
