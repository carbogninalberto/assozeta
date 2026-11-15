package service

import (
	"errors"
	"net/http"
	"testing"

	smithy "github.com/aws/smithy-go"
	smithyhttp "github.com/aws/smithy-go/transport/http"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentobjects"
)

// botocoreTestError is an S3 client error answer: the HTTP status and the
// parsed error code and message.
func botocoreTestError(status int, code, message string) error {
	return &smithyhttp.ResponseError{Response: &smithyhttp.Response{Response: &http.Response{StatusCode: status}},
		Err: &smithy.GenericAPIError{Code: code, Message: message}}
}

// Expected texts: botocore.exceptions.ClientError's str() for an answer the
// legacy retry mode does not retry.
func TestBotocoreClientError(t *testing.T) {
	for _, tc := range []struct {
		operation, want string
		err             error
		head, ok        bool
	}{
		// A HEAD answer has no body: the code is the status, the message its
		// Python reason phrase.
		{"HeadObject", "An error occurred (403) when calling the HeadObject operation: Forbidden",
			botocoreTestError(http.StatusForbidden, "", ""), true, true},
		{"HeadObject", "An error occurred (404) when calling the HeadObject operation: Not Found",
			botocoreTestError(http.StatusNotFound, "", ""), true, true},
		{"HeadObject", "An error occurred (418) when calling the HeadObject operation: I'm a Teapot",
			botocoreTestError(http.StatusTeapot, "", ""), true, true},
		{"HeadObject", "An error occurred (400) when calling the HeadObject operation: Bad Request",
			botocoreTestError(http.StatusBadRequest, "", ""), true, true},
		// Other operations print the parsed code and message.
		{"PutObject", "An error occurred (AccessDenied) when calling the PutObject operation: Access Denied.",
			botocoreTestError(http.StatusForbidden, "AccessDenied", "Access Denied."), false, true},
		{"PutObjectAcl", "An error occurred (AccessDenied) when calling the PutObjectAcl operation: Access Denied.",
			botocoreTestError(http.StatusForbidden, "AccessDenied", "Access Denied."), false, true},
		{"HeadObject", "An error occurred (501) when calling the HeadObject operation: Not Implemented",
			botocoreTestError(http.StatusNotImplemented, "", ""), true, true},
		{"PutObject", "An error occurred (NotImplemented) when calling the PutObject operation: A header you provided implies functionality that is not implemented.",
			botocoreTestError(http.StatusNotImplemented, "NotImplemented", "A header you provided implies functionality that is not implemented."), false, true},
		// botocore retries these: they stay refused.
		{"HeadObject", "", botocoreTestError(http.StatusServiceUnavailable, "", ""), true, false},
		{"PutObject", "", botocoreTestError(http.StatusServiceUnavailable, "ServiceUnavailable", "Please reduce your request rate."), false, false},
		{"HeadObject", "", botocoreTestError(http.StatusTooManyRequests, "", ""), true, false},
		{"PutObject", "", botocoreTestError(http.StatusInternalServerError, "InternalError", "We encountered an internal error."), false, false},
		{"PutObject", "", botocoreTestError(http.StatusBadGateway, "BadGateway", "Bad gateway."), false, false},
		{"PutObject", "", botocoreTestError(http.StatusBadRequest, "RequestTimeout", "Your socket connection to the server was not read from or written to within the timeout period."), false, false},
		{"PutObject", "", botocoreTestError(http.StatusBadRequest, "Throttling", "Please reduce your request rate."), false, false},
		// 3xx answers and connection failures have no ClientError text.
		{"HeadObject", "", botocoreTestError(http.StatusMovedPermanently, "", ""), true, false},
		{"HeadObject", "", errors.New("dial tcp: connection refused"), true, false},
		{"PutObject", "", errors.New("dial tcp: connection refused"), false, false},
	} {
		text, ok := botocoreClientError(tc.err, tc.operation, tc.head)
		if ok != tc.ok || text != tc.want {
			t.Errorf("%s head=%v: %q, %v; want %q, %v", tc.operation, tc.head, text, ok, tc.want, tc.ok)
		}
	}
}

// The document storage's Exists already answers a HEAD client error with
// botocore's text.
func TestBotocoreClientErrorHeadStatus(t *testing.T) {
	text, ok := botocoreClientError(&documentobjects.HeadStatusError{Status: http.StatusForbidden, Reason: "Forbidden"}, "HeadObject", true)
	if !ok || text != "An error occurred (403) when calling the HeadObject operation: Forbidden" {
		t.Fatalf("head status: %q, %v", text, ok)
	}
	if _, ok := botocoreClientError(&documentobjects.HeadStatusError{Status: http.StatusForbidden, Reason: "Forbidden"}, "PutObject", false); ok {
		t.Fatal("a HEAD answer accepted as a PutObject answer")
	}
}
