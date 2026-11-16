package service

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"strings"
	"syscall"
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws/retry"
	"github.com/aws/smithy-go"
	smithyhttp "github.com/aws/smithy-go/transport/http"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentobjects"
)

// storageResponseError is the error the S3 client returns for one answer of an
// operation: the status and the parsed error, if any.
func storageResponseError(operation string, status int, api smithy.APIError) error {
	var inner error
	if api != nil {
		inner = api
	}
	return &smithy.OperationError{ServiceID: "S3", OperationName: operation, Err: consumerTestResponseError(status, inner)}
}

// storageOperationError is the error the S3 client returns for one failed
// request of an operation (no HTTP answer: the response error carries status 0
// like the SDK's).
func storageOperationError(operation string, err error) error {
	return &smithy.OperationError{ServiceID: "S3", OperationName: operation, Err: consumerTestResponseError(0, err)}
}

// storageTimeoutError is a net.Error whose Timeout is true.
type storageTimeoutError struct{}

func (storageTimeoutError) Error() string   { return "i/o timeout" }
func (storageTimeoutError) Timeout() bool   { return true }
func (storageTimeoutError) Temporary() bool { return true }

// storageRequestURL is the URL of an S3 request, with the x-id query parameter
// the client adds and boto3 does not.
func storageRequestURL(path string) string {
	return "http://objects.example.test" + path + "?x-id=PutObject"
}

// storageTransportError is a request that never received an answer.
func storageTransportError(operation, path string, err error) error {
	return storageOperationError(operation, &smithyhttp.RequestSendError{
		Err: &url.Error{Op: strings.TrimSuffix(operation, "Object"), URL: path, Err: err}})
}

func TestBotocoreFinalErrorTextAnswers(t *testing.T) {
	for _, tc := range []struct {
		name      string
		operation string
		err       error
		want      string
	}{
		// An answer botocore does not retry: code and message as parsed.
		{"denied", "PutObject", storageResponseError("PutObject", http.StatusForbidden,
			consumerTestAPIError{"AccessDenied", "Access Denied"}), "An error occurred (AccessDenied) when calling the PutObject operation: Access Denied"},
		{"acl", "PutObjectAcl", storageResponseError("PutObjectAcl", http.StatusForbidden,
			consumerTestAPIError{"AccessDenied", "Access Denied"}), "An error occurred (AccessDenied) when calling the PutObjectAcl operation: Access Denied"},
		// botocore reads "Unknown" when the body carries no message.
		{"no-message", "PutObject", storageResponseError("PutObject", http.StatusForbidden,
			consumerTestAPIError{"AccessDenied", ""}), "An error occurred (AccessDenied) when calling the PutObject operation: Unknown"},
		// The placeholder the S3 deserializer uses for a missing message is no
		// message either.
		{"placeholder-message", "PutObject", storageResponseError("PutObject", http.StatusForbidden,
			consumerTestAPIError{"AccessDenied", "UnknownError"}), "An error occurred (AccessDenied) when calling the PutObject operation: Unknown"},
		// No parseable code (an empty or HTML body): the status and its phrase.
		{"no-code", "PutObject", storageResponseError("PutObject", http.StatusForbidden,
			consumerTestAPIError{"Forbidden", "Forbidden"}), "An error occurred (403) when calling the PutObject operation: Forbidden"},
		{"head-403", "HeadObject", storageResponseError("HeadObject", http.StatusForbidden, nil),
			"An error occurred (403) when calling the HeadObject operation: Forbidden"},
		{"head-404", "HeadObject", storageResponseError("HeadObject", http.StatusNotFound, nil),
			"An error occurred (404) when calling the HeadObject operation: Not Found"},
		{"head-418", "HeadObject", storageResponseError("HeadObject", http.StatusTeapot,
			consumerTestAPIError{"Forbidden", "Forbidden"}), "An error occurred (418) when calling the HeadObject operation: I'm a Teapot"},
		{"head-501", "HeadObject", storageResponseError("HeadObject", http.StatusNotImplemented, nil),
			"An error occurred (501) when calling the HeadObject operation: Not Implemented"},
		// The operation boto3 names wins over the SDK's request (a multipart save).
		{"other-operation", "CreateMultipartUpload", storageResponseError("PutObject", http.StatusForbidden,
			consumerTestAPIError{"AccessDenied", "denied"}), "An error occurred (AccessDenied) when calling the CreateMultipartUpload operation: denied"},
		// documentobjects.Exists already answers a HEAD client error.
		{"head-status", "HeadObject", &documentobjects.HeadStatusError{Status: http.StatusForbidden, Reason: "Forbidden"},
			"An error occurred (403) when calling the HeadObject operation: Forbidden"},

		// An answer botocore retries: the ClientError carries the retry count.
		{"server", "PutObject", storageResponseError("PutObject", http.StatusServiceUnavailable,
			consumerTestAPIError{"SlowDown", "Reduce request rate"}),
			"An error occurred (SlowDown) when calling the PutObject operation (reached max retries: 4): Reduce request rate"},
		{"server-no-code", "PutObject", storageResponseError("PutObject", http.StatusServiceUnavailable,
			consumerTestAPIError{"ServiceUnavailable", "Service Unavailable"}),
			"An error occurred (503) when calling the PutObject operation (reached max retries: 4): Service Unavailable"},
		{"server-bodyless", "PutObject", storageResponseError("PutObject", http.StatusInternalServerError, nil),
			"An error occurred (500) when calling the PutObject operation (reached max retries: 4): Internal Server Error"},
		{"throttled-status", "PutObject", storageResponseError("PutObject", http.StatusTooManyRequests, nil),
			"An error occurred (429) when calling the PutObject operation (reached max retries: 4): Too Many Requests"},
		{"throttled-status-code", "PutObject", storageResponseError("PutObject", http.StatusTooManyRequests,
			consumerTestAPIError{"TooManyRequests", "Too many requests"}),
			"An error occurred (TooManyRequests) when calling the PutObject operation (reached max retries: 4): Too many requests"},
		// A 408 is no retried status: only S3's own 400 RequestTimeout is.
		{"request-timeout-status", "PutObject", storageResponseError("PutObject", http.StatusRequestTimeout, nil),
			"An error occurred (408) when calling the PutObject operation: Request Timeout"},
		{"internal", "PutObject", storageResponseError("PutObject", http.StatusInternalServerError,
			consumerTestAPIError{"InternalError", "We encountered an internal error."}),
			"An error occurred (InternalError) when calling the PutObject operation (reached max retries: 4): We encountered an internal error."},
		{"bad-gateway", "PutObject", storageResponseError("PutObject", http.StatusBadGateway,
			consumerTestAPIError{"BadGateway", "Bad gateway."}),
			"An error occurred (BadGateway) when calling the PutObject operation (reached max retries: 4): Bad gateway."},
		// Python's http.client names no 509: botocore's message is empty.
		{"limit", "PutObject", storageResponseError("PutObject", 509, nil),
			"An error occurred (509) when calling the PutObject operation (reached max retries: 4): "},
		{"head-503", "HeadObject", storageResponseError("HeadObject", http.StatusServiceUnavailable, nil),
			"An error occurred (503) when calling the HeadObject operation (reached max retries: 4): Service Unavailable"},
		{"head-509", "HeadObject", storageResponseError("HeadObject", 509, nil),
			"An error occurred (509) when calling the HeadObject operation (reached max retries: 4): "},
		// A 400 the S3 policies retry (RequestTimeout and BadDigest).
		{"request-timeout", "PutObject", storageResponseError("PutObject", http.StatusBadRequest,
			consumerTestAPIError{"RequestTimeout", "Your socket connection to the server was not read from or written to within the timeout period."}),
			"An error occurred (RequestTimeout) when calling the PutObject operation (reached max retries: 4): Your socket connection to the server was not read from or written to within the timeout period."},
		{"bad-digest", "PutObject", storageResponseError("PutObject", http.StatusBadRequest,
			consumerTestAPIError{"BadDigest", "The Content-MD5 you specified did not match what we received."}),
			"An error occurred (BadDigest) when calling the PutObject operation (reached max retries: 4): The Content-MD5 you specified did not match what we received."},
		{"throttling", "PutObject", storageResponseError("PutObject", http.StatusBadRequest,
			consumerTestAPIError{"Throttling", "Please reduce your request rate."}),
			"An error occurred (Throttling) when calling the PutObject operation (reached max retries: 4): Please reduce your request rate."},
		// The retried codes are the 400 answers of the policies: the same code
		// on another status is not one.
		{"throttling-403", "PutObject", storageResponseError("PutObject", http.StatusForbidden,
			consumerTestAPIError{"Throttling", "Please reduce your request rate."}),
			"An error occurred (Throttling) when calling the PutObject operation: Please reduce your request rate."},
		// The S3 region redirector retries a location answer once and the
		// final answer carries no retry info.
		{"region-redirect", "PutObject", storageResponseError("PutObject", http.StatusMovedPermanently,
			consumerTestAPIError{"PermanentRedirect", "The bucket you are attempting to access must be addressed using the specified endpoint."}),
			"An error occurred (PermanentRedirect) when calling the PutObject operation: The bucket you are attempting to access must be addressed using the specified endpoint."},
		// Any answer once the SDK's own retryer gave up.
		{"gave-up", "PutObject", &retry.MaxAttemptsError{Attempt: 5, Err: storageResponseError("PutObject", http.StatusForbidden,
			consumerTestAPIError{"AccessDenied", "denied"})},
			"An error occurred (AccessDenied) when calling the PutObject operation (reached max retries: 4): denied"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			text, ok := botocoreFinalErrorText(tc.err, tc.operation)
			if !ok || text != tc.want {
				t.Fatalf("%q, %v; want %q, true", text, ok, tc.want)
			}
		})
	}
}

func TestBotocoreFinalErrorTextTransport(t *testing.T) {
	refused := storageTransportError("PutObject", storageRequestURL("/media/a.pdf"), &net.OpError{Op: "dial",
		Err: os.NewSyscallError("connect", syscall.ECONNREFUSED)})
	dns := storageTransportError("HeadObject", "http://objects.invalid/media/a.pdf", &net.OpError{Op: "dial",
		Err: &net.DNSError{Err: "no such host", Name: "objects.invalid", IsNotFound: true}})
	connectTimeout := storageTransportError("PutObject", storageRequestURL("/media/a.pdf"), &net.OpError{Op: "dial",
		Err: storageTimeoutError{}})
	for _, tc := range []struct {
		name string
		err  error
		want string
	}{
		// Connection refused and DNS failure: botocore's EndpointConnectionError.
		{"refused", refused, `Could not connect to the endpoint URL: "http://objects.example.test/media/a.pdf"`},
		{"dns", dns, `Could not connect to the endpoint URL: "http://objects.invalid/media/a.pdf"`},
		// A connection that timed out while being opened.
		{"connect-timeout", connectTimeout, `Connect timeout on endpoint URL: "http://objects.example.test/media/a.pdf"`},
		// An answer that timed out after the connection was opened: Go's
		// response-header timeout and its read deadline.
		{"read-timeout", storageTransportError("HeadObject", "http://objects.example.test/media/a.pdf", os.ErrDeadlineExceeded),
			`Read timeout on endpoint URL: "http://objects.example.test/media/a.pdf"`},
		// A deadline error wrapped so the url.Error does not report a timeout.
		{"wrapped-deadline", storageTransportError("PutObject", "http://objects.example.test/media/a.pdf",
			fmt.Errorf("read: %w", os.ErrDeadlineExceeded)),
			`Read timeout on endpoint URL: "http://objects.example.test/media/a.pdf"`},
		{"header-timeout", storageTransportError("HeadObject", "http://objects.example.test/media/a.pdf", storageTimeoutError{}),
			`Read timeout on endpoint URL: "http://objects.example.test/media/a.pdf"`},
		// The connection closed before a valid answer.
		{"unexpected-eof", storageTransportError("PutObject", storageRequestURL("/media/a.pdf"), io.ErrUnexpectedEOF),
			`Connection was closed before we received a valid response from endpoint URL: "http://objects.example.test/media/a.pdf".`},
		{"reset", storageTransportError("GetObject", "http://objects.example.test/media/a.pdf",
			os.NewSyscallError("read", syscall.ECONNRESET)),
			`Connection was closed before we received a valid response from endpoint URL: "http://objects.example.test/media/a.pdf".`},
		{"eof", storageTransportError("GetObject", "http://objects.example.test/media/a.pdf", io.EOF),
			`Connection was closed before we received a valid response from endpoint URL: "http://objects.example.test/media/a.pdf".`},
		// A close Go reports by message only (HTTP/2 idle connections).
		{"idle-closed", storageTransportError("PutObject", "http://objects.example.test/media/a.pdf",
			errors.New("http2: server closed idle connection")),
			`Connection was closed before we received a valid response from endpoint URL: "http://objects.example.test/media/a.pdf".`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			text, ok := botocoreFinalErrorText(tc.err, "PutObject")
			if !ok || text != tc.want {
				t.Fatalf("%q, %v; want %q, true", text, ok, tc.want)
			}
		})
	}
	// A retried transport error carries no retry info: botocore raises the
	// connection error itself.
	gaveUp := &retry.MaxAttemptsError{Attempt: 5, Err: refused}
	if text, ok := botocoreFinalErrorText(gaveUp, "PutObject"); !ok ||
		text != `Could not connect to the endpoint URL: "http://objects.example.test/media/a.pdf"` {
		t.Fatalf("gave-up transport: %q, %v", text, ok)
	}
}

// An answer cut short after its status line has no *url.Error: the URL comes
// from the response's request.
func TestBotocoreFinalErrorTextClosedAnswer(t *testing.T) {
	request, err := http.NewRequest(http.MethodPost, storageRequestURL("/media/a.pdf"), nil)
	if err != nil {
		t.Fatal(err)
	}
	response := &smithyhttp.ResponseError{
		Response: &smithyhttp.Response{Response: &http.Response{StatusCode: http.StatusOK, Request: request}},
		Err:      io.ErrUnexpectedEOF,
	}
	text, ok := botocoreFinalErrorText(&smithy.OperationError{ServiceID: "S3", OperationName: "PutObject", Err: response}, "PutObject")
	if !ok || text != `Connection was closed before we received a valid response from endpoint URL: "http://objects.example.test/media/a.pdf".` {
		t.Fatalf("closed answer: %q, %v", text, ok)
	}
	// A multipart save's first request carries "?uploads".
	text, ok = botocoreFinalErrorTextSuffixed(&smithy.OperationError{ServiceID: "S3", OperationName: "PutObject", Err: response},
		"CreateMultipartUpload", "?uploads")
	if !ok || text != `Connection was closed before we received a valid response from endpoint URL: "http://objects.example.test/media/a.pdf?uploads".` {
		t.Fatalf("multipart URL: %q, %v", text, ok)
	}
}

// The multipart suffix joins the query boto3's own request carries.
func TestBotocoreRequestURLSuffix(t *testing.T) {
	for _, tc := range []struct {
		name, requestURL, suffix, want string
	}{
		{"plain", "http://host/b/k", "", "http://host/b/k"},
		{"empty-query", "http://host/b/k?x-id=CreateMultipartUpload", "?uploads", "http://host/b/k?uploads"},
		{"query", "http://host/b/k?versionId=1&x-id=CreateMultipartUpload", "?uploads",
			"http://host/b/k?versionId=1&uploads"},
		{"unparsable", "http://[::1", "?uploads", "http://[::1?uploads"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := botocoreRequestURL(tc.requestURL, tc.suffix); got != tc.want {
				t.Fatalf("%q, want %q", got, tc.want)
			}
		})
	}
}

func TestBotocoreFinalErrorTextNotReproduced(t *testing.T) {
	cancelled := storageOperationError("PutObject", &url.Error{Op: "Put", URL: "http://host/k", Err: context.Canceled})
	for label, err := range map[string]error{
		"cancelled":      cancelled,
		"bare-cancelled": context.Canceled,
		// Neither an answer nor a transport error: the SDK's own configuration
		// errors, which botocore words differently.
		"configuration": errors.New("document storage is not configured"),
		// A close without a request URL cannot name botocore's endpoint.
		"closed-without-url": errors.New("connection reset by peer"),
		"closed-without-request": &smithy.OperationError{ServiceID: "S3", OperationName: "PutObject",
			Err: consumerTestResponseError(http.StatusOK, io.ErrUnexpectedEOF)},
	} {
		if text, ok := botocoreFinalErrorText(err, "PutObject"); ok {
			t.Fatalf("%s reproduced: %q", label, text)
		}
	}
}

func TestBotocoreClientErrorText(t *testing.T) {
	if text, ok := botocoreClientErrorText(storageResponseError("HeadObject", http.StatusBadRequest, nil), "HeadObject"); !ok ||
		text != "An error occurred (400) when calling the HeadObject operation: Bad Request" {
		t.Fatalf("head: %q %v", text, ok)
	}
	denied := storageResponseError("PutObject", http.StatusForbidden, consumerTestAPIError{"AccessDenied", "Access Denied"})
	if text, ok := botocoreClientErrorText(denied, "PutObject"); !ok ||
		text != "An error occurred (AccessDenied) when calling the PutObject operation: Access Denied" {
		t.Fatalf("denied: %q %v", text, ok)
	}
	if _, ok := botocoreClientErrorText(errors.New("dial tcp: connection refused"), "PutObject"); ok {
		t.Fatal("a transport error is not a ClientError answer")
	}
}

func TestDocumentSaveFailureText(t *testing.T) {
	denied := storageResponseError("PutObject", http.StatusForbidden, consumerTestAPIError{"AccessDenied", "denied"})
	if text, ok := documentSaveFailureText(denied, 4); !ok ||
		text != "An error occurred (AccessDenied) when calling the PutObject operation: denied" {
		t.Fatalf("small body: %q %v", text, ok)
	}
	// s3transfer splits content of multipart_threshold bytes: boto3 names
	// CreateMultipartUpload.
	if text, ok := documentSaveFailureText(denied, consumerStorageMultipartThreshold); !ok ||
		text != "An error occurred (AccessDenied) when calling the CreateMultipartUpload operation: denied" {
		t.Fatalf("multipart body: %q %v", text, ok)
	}
	// A retried answer of such a body names the same operation.
	slow := storageResponseError("PutObject", http.StatusServiceUnavailable, consumerTestAPIError{"SlowDown", "Reduce request rate"})
	if text, ok := documentSaveFailureText(slow, consumerStorageMultipartThreshold); !ok ||
		text != "An error occurred (SlowDown) when calling the CreateMultipartUpload operation (reached max retries: 4): Reduce request rate" {
		t.Fatalf("multipart retried: %q %v", text, ok)
	}
	// A transport error of such a body names the request boto3 makes.
	transport := storageTransportError("PutObject", storageRequestURL("/media/a.pdf"), &net.OpError{Op: "dial",
		Err: os.NewSyscallError("connect", syscall.ECONNREFUSED)})
	if text, ok := documentSaveFailureText(transport, consumerStorageMultipartThreshold); !ok ||
		text != `Could not connect to the endpoint URL: "http://objects.example.test/media/a.pdf?uploads"` {
		t.Fatalf("multipart transport: %q %v", text, ok)
	}
	if text, ok := documentSaveFailureText(transport, 4); !ok || strings.Contains(text, "uploads") {
		t.Fatalf("small transport: %q %v", text, ok)
	}
	// The exists check before the save is a single HEAD, whatever the content
	// size: boto3 names HeadObject, not CreateMultipartUpload.
	head := &documentExistsError{err: storageResponseError("HeadObject", http.StatusServiceUnavailable, nil)}
	if text, ok := documentSaveFailureText(head, consumerStorageMultipartThreshold); !ok ||
		text != "An error occurred (503) when calling the HeadObject operation (reached max retries: 4): Service Unavailable" {
		t.Fatalf("multipart exists: %q %v", text, ok)
	}
	headStatus := &documentExistsError{err: &documentobjects.HeadStatusError{Status: http.StatusForbidden, Reason: "Forbidden"}}
	if text, ok := documentSaveFailureText(headStatus, consumerStorageMultipartThreshold); !ok ||
		text != "An error occurred (403) when calling the HeadObject operation: Forbidden" {
		t.Fatalf("multipart exists status: %q %v", text, ok)
	}
	// Not a storage answer.
	if _, ok := documentSaveFailureText(context.Canceled, 4); ok {
		t.Fatal("a cancelled request is not a storage answer")
	}
}
