package service

import (
	"errors"
	"testing"

	"github.com/aws/aws-sdk-go-v2/aws/retry"
	"github.com/aws/smithy-go"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentobjects"
)

// storagePutObject is the error the S3 client returns for a PutObject answer.
func storagePutObject(status int, api smithy.APIError) error {
	return &smithy.OperationError{ServiceID: "S3", OperationName: "PutObject", Err: consumerTestResponseError(status, api)}
}

func TestBotocoreClientErrorText(t *testing.T) {
	head := &documentobjects.HeadStatusError{Status: 400, Reason: "Bad Request"}
	if text, ok := botocoreClientErrorText(head); !ok || text != "An error occurred (400) when calling the HeadObject operation: Bad Request" {
		t.Fatalf("head: %q %v", text, ok)
	}
	denied := storagePutObject(403, consumerTestAPIError{"AccessDenied", "Access Denied"})
	if text, ok := botocoreClientErrorText(denied); !ok || text != "An error occurred (AccessDenied) when calling the PutObject operation: Access Denied" {
		t.Fatalf("denied: %q %v", text, ok)
	}
	empty := storagePutObject(403, consumerTestAPIError{"AccessDenied", ""})
	if text, ok := botocoreClientErrorText(empty); !ok || text != "An error occurred (AccessDenied) when calling the PutObject operation: Unknown" {
		t.Fatalf("empty: %q %v", text, ok)
	}
	for label, err := range map[string]error{
		"plain":            errors.New("dial tcp: connection refused"),
		"server":           storagePutObject(503, consumerTestAPIError{"SlowDown", "x"}),
		"throttled":        storagePutObject(429, consumerTestAPIError{"Throttling", "x"}),
		"timeout":          storagePutObject(400, consumerTestAPIError{"RequestTimeout", "x"}),
		"bad-digest":       storagePutObject(400, consumerTestAPIError{"BadDigest", "x"}),
		"region-redirect":  storagePutObject(400, consumerTestAPIError{"AuthorizationHeaderMalformed", "x"}),
		"raw-head":         &smithy.OperationError{ServiceID: "S3", OperationName: "HeadObject", Err: consumerTestResponseError(400, consumerTestAPIError{"400", "Bad Request"})},
		"reached-attempts": &retry.MaxAttemptsError{Attempt: 5, Err: storagePutObject(503, consumerTestAPIError{"SlowDown", "x"})},
	} {
		if text, ok := botocoreClientErrorText(err); ok {
			t.Fatalf("%s should not be reproduced: %q", label, text)
		}
	}
}

func TestDocumentSaveFailureText(t *testing.T) {
	if text, ok := documentSaveFailureText(storagePutObject(403, consumerTestAPIError{"AccessDenied", "denied"}), 4); !ok ||
		text != "An error occurred (AccessDenied) when calling the PutObject operation: denied" {
		t.Fatalf("small body: %q %v", text, ok)
	}
	// s3transfer splits content of multipart_threshold bytes: boto3 names another operation.
	if text, ok := documentSaveFailureText(storagePutObject(403, consumerTestAPIError{"AccessDenied", "denied"}), 8<<20); ok {
		t.Fatalf("multipart body should not be reproduced: %q", text)
	}
	// The exists check is a single HEAD whatever the size.
	head := &documentobjects.HeadStatusError{Status: 400, Reason: "Bad Request"}
	if text, ok := documentSaveFailureText(head, 8<<20); !ok ||
		text != "An error occurred (400) when calling the HeadObject operation: Bad Request" {
		t.Fatalf("head of a multipart body: %q %v", text, ok)
	}
}
