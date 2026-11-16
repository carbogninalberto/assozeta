package service

import (
	"context"
	"errors"
	"net"
	"net/http"
	"os"
	"syscall"
	"testing"
	"time"

	"github.com/aws/smithy-go"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentobjects"
)

// docmanagerTestObjects is default_storage for one retrieval: the exists
// answer and the GetObject outcome (an existing object is never opened here).
type docmanagerTestObjects struct {
	exists    bool
	existsErr error
	open      error
}

func (docmanagerTestObjects) Location() string { return "" }

func (o docmanagerTestObjects) Exists(context.Context, string) (bool, error) {
	return o.exists, o.existsErr
}

func (o docmanagerTestObjects) Open(context.Context, string) (*documentobjects.Object, error) {
	return nil, o.open
}

// An inline disposition without a filename is the None content_disposition_header
// returns, which the response stores as the text 'None'.
func TestDocmanagerDispositionNone(t *testing.T) {
	if value := docmanagerDisposition(false, ""); value == nil || *value != "None" {
		t.Fatalf("inline without a filename: %v", value)
	}
	if _, ok := contentDisposition(false, ""); ok {
		t.Fatal("content_disposition_header must report no header")
	}
	if value := docmanagerDisposition(true, ""); value == nil || *value != "attachment" {
		t.Fatalf("attachment without a filename: %v", value)
	}
	if value := docmanagerDisposition(false, "referto.pdf"); value == nil || *value != `inline; filename="referto.pdf"` {
		t.Fatalf("inline with a filename: %v", value)
	}
}

// docmanagerTestDocument is the document each retrieval streams.
func docmanagerTestDocument() *domain.StoredDocument {
	return &domain.StoredDocument{ID: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", Filename: "referto.pdf",
		Created: time.Date(2024, 1, 1, 0, 0, 0, 0, time.UTC)}
}

// default_storage.exists re-raises every error other than 404: _stream_file_response
// answers 500 {'exception': str(e)} with botocore's final text for a client error,
// a retried answer and a transport error, as it does for a missing key.
func TestDocmanagerStreamExistsAnswers(t *testing.T) {
	requestURL := storageRequestURL("/1704067200.0/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee/referto.pdf")
	for _, tt := range []struct {
		name string
		err  error
		want string
	}{
		{"client", &documentobjects.HeadStatusError{Status: http.StatusForbidden, Reason: "Forbidden"},
			"An error occurred (403) when calling the HeadObject operation: Forbidden"},
		{"retried", storageResponseError("HeadObject", http.StatusServiceUnavailable, nil),
			"An error occurred (503) when calling the HeadObject operation (reached max retries: 4): Service Unavailable"},
		{"refused", storageTransportError("HeadObject", requestURL, &net.OpError{Op: "dial",
			Err: os.NewSyscallError("connect", syscall.ECONNREFUSED)}),
			`Could not connect to the endpoint URL: "http://objects.example.test/1704067200.0/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee/referto.pdf"`},
	} {
		t.Run(tt.name, func(t *testing.T) {
			reads := NewDocmanagerReads(nil, docmanagerTestObjects{existsErr: tt.err}, nil, "", time.Now)
			reply, err := reads.stream(context.Background(), docmanagerTestDocument(), true, "true", nil, nil)
			if err != nil || reply == nil || reply.Status != 500 {
				t.Fatalf("reply %+v, err %v", reply, err)
			}
			if body, ok := reply.Body.(map[string]string); !ok || body["exception"] != tt.want {
				t.Fatalf("body %+v, want %q", reply.Body, tt.want)
			}
		})
	}
}

// _stream_file_response's GetObject: a client error botocore raises once, a
// retried answer and a transport error are the view's 500 {'exception': str(e)},
// as NoSuchKey already is.
func TestDocmanagerStreamGetObjectAnswers(t *testing.T) {
	requestURL := storageRequestURL("/1704067200.0/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee/referto.pdf")
	for _, tt := range []struct {
		name string
		err  error
		want string
	}{
		{"missing", &documentobjects.NoSuchKeyError{Message: "The specified key does not exist."},
			"An error occurred (NoSuchKey) when calling the GetObject operation: The specified key does not exist."},
		{"client", &smithy.OperationError{ServiceID: "S3", OperationName: "GetObject",
			Err: consumerTestResponseError(403, consumerTestAPIError{"AccessDenied", "Access Denied"})},
			"An error occurred (AccessDenied) when calling the GetObject operation: Access Denied"},
		{"retried", storageResponseError("GetObject", http.StatusInternalServerError, consumerTestAPIError{"InternalError", "We encountered an internal error."}),
			"An error occurred (InternalError) when calling the GetObject operation (reached max retries: 4): We encountered an internal error."},
		{"refused", storageTransportError("GetObject", requestURL, &net.OpError{Op: "dial",
			Err: os.NewSyscallError("connect", syscall.ECONNREFUSED)}),
			`Could not connect to the endpoint URL: "http://objects.example.test/1704067200.0/aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee/referto.pdf"`},
	} {
		t.Run(tt.name, func(t *testing.T) {
			reads := NewDocmanagerReads(nil, docmanagerTestObjects{exists: true, open: tt.err}, nil, "", time.Now)
			reply, err := reads.stream(context.Background(), docmanagerTestDocument(), true, "true", nil, nil)
			if err != nil || reply == nil || reply.Status != 500 {
				t.Fatalf("reply %+v, err %v", reply, err)
			}
			if body, ok := reply.Body.(map[string]string); !ok || body["exception"] != tt.want {
				t.Fatalf("body %+v, want %q", reply.Body, tt.want)
			}
		})
	}
	// A cancelled request is not a storage answer: the error is re-raised.
	cancelled := context.Canceled
	reads := NewDocmanagerReads(nil, docmanagerTestObjects{exists: true, open: cancelled}, nil, "", time.Now)
	if reply, err := reads.stream(context.Background(), docmanagerTestDocument(), true, "true", nil, nil); !errors.Is(err, cancelled) || reply != nil {
		t.Fatalf("cancelled request must be re-raised: %+v %v", reply, err)
	}
}
