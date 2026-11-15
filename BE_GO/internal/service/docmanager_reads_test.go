package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/aws/smithy-go"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentobjects"
)

// docmanagerTestObjects is default_storage for one retrieval: the exists
// answer and the GetObject outcome (an existing object is never opened here).
type docmanagerTestObjects struct {
	exists bool
	open   error
}

func (docmanagerTestObjects) Location() string { return "" }

func (o docmanagerTestObjects) Exists(context.Context, string) (bool, error) { return o.exists, nil }

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

// _stream_file_response's GetObject: a client error botocore raises once is the
// view's 500 {'exception': str(e)}, as NoSuchKey already is; a transport error
// stays with Django.
func TestDocmanagerStreamGetObjectAnswers(t *testing.T) {
	document := &domain.StoredDocument{ID: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", Filename: "referto.pdf",
		Created: time.Date(2024, 1, 1, 0, 0, 0, 0, time.UTC)}
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
	} {
		t.Run(tt.name, func(t *testing.T) {
			reads := NewDocmanagerReads(nil, docmanagerTestObjects{exists: true, open: tt.err}, nil, "", time.Now)
			reply, err := reads.stream(context.Background(), document, true, "true", nil, nil)
			if err != nil || reply == nil || reply.Status != 500 {
				t.Fatalf("reply %+v, err %v", reply, err)
			}
			if body, ok := reply.Body.(map[string]string); !ok || body["exception"] != tt.want {
				t.Fatalf("body %+v, want %q", reply.Body, tt.want)
			}
		})
	}
	reads := NewDocmanagerReads(nil, docmanagerTestObjects{exists: true, open: errors.New("dial tcp: connection refused")}, nil, "",
		time.Now)
	if reply, err := reads.stream(context.Background(), document, true, "true", nil, nil); !errors.Is(err, domain.ErrDocmanagerNotPorted) {
		t.Fatalf("transport error must stay a refusal: %+v %v", reply, err)
	}
}
