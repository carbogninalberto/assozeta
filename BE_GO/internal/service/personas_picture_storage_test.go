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

	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentobjects"
)

// personaPictureObjects is default_storage for one picture upload: Exists,
// Put and PutACL may each fail with the error the S3 client returns.
type personaPictureObjects struct {
	existsErr, putErr, aclErr error
	aclCalls                  int
}

func (personaPictureObjects) Location() string { return "tenant-files" }

func (o personaPictureObjects) Exists(context.Context, string) (bool, error) {
	return false, o.existsErr
}

func (o personaPictureObjects) Put(context.Context, string, []byte, string, string) error {
	return o.putErr
}

func (o *personaPictureObjects) PutACL(context.Context, string, string) error {
	o.aclCalls++
	return o.aclErr
}

func personaPictureWrites(objects PersonaPictureObjects, overwrite bool) *PersonaWrites {
	writes := NewPersonaWrites(nil, nil, func() time.Time { return time.Unix(1700000000, 0) }, nil)
	return writes.WithPictures(objects, PersonaPictureSettings{Overwrite: overwrite, UseObjectACL: true,
		Endpoint: "https://objects.example.test", Bucket: "media"})
}

// _store_picture answers every storage exception as the serializer's 400 with
// botocore's final text.
func TestPersonaPictureUploadStorageFailures(t *testing.T) {
	denied := storageResponseError("PutObject", http.StatusForbidden, consumerTestAPIError{"AccessDenied", "Access Denied."})
	headDenied := &documentobjects.HeadStatusError{Status: http.StatusForbidden, Reason: "Forbidden"}
	headRetried := storageResponseError("HeadObject", http.StatusServiceUnavailable, nil)
	headRefused := storageTransportError("HeadObject", storageRequestURL("/tenant-files/associates/a/picture_1700000000.0.jpg"),
		&net.OpError{Op: "dial", Err: os.NewSyscallError("connect", syscall.ECONNREFUSED)})
	aclDenied := storageResponseError("PutObjectAcl", http.StatusForbidden, consumerTestAPIError{"AccessDenied", "Access Denied."})
	for _, tc := range []struct {
		name      string
		overwrite bool
		exists    error
		put       error
		acl       error
		aclCalls  int
		want      string
	}{
		{"head", false, headDenied, nil, nil, 0,
			"Error processing image: An error occurred (403) when calling the HeadObject operation: Forbidden"},
		{"head-retried", false, headRetried, nil, nil, 0,
			"Error processing image: An error occurred (503) when calling the HeadObject operation (reached max retries: 4): Service Unavailable"},
		{"head-refused", false, headRefused, nil, nil, 0,
			`Error processing image: Could not connect to the endpoint URL: "http://objects.example.test/tenant-files/associates/a/picture_1700000000.0.jpg"`},
		{"put", true, nil, denied, nil, 0,
			"Error processing image: An error occurred (AccessDenied) when calling the PutObject operation: Access Denied."},
		{"acl", true, nil, nil, aclDenied, 1,
			"Error processing image: An error occurred (AccessDenied) when calling the PutObjectAcl operation: Access Denied."},
	} {
		t.Run(tc.name, func(t *testing.T) {
			objects := &personaPictureObjects{existsErr: tc.exists, putErr: tc.put, aclErr: tc.acl}
			writes := personaPictureWrites(objects, tc.overwrite)
			_, err := writes.uploadPicture(context.Background(), []byte("picture"), "a1000000-0000-4000-8000-000000000001")
			var invalid *PersonaPictureInvalid
			if !errors.As(err, &invalid) || invalid.Text != tc.want {
				t.Fatalf("%v, want %q", err, tc.want)
			}
			if objects.aclCalls != tc.aclCalls {
				t.Fatalf("PutACL called %d times, want %d", objects.aclCalls, tc.aclCalls)
			}
		})
	}
	// A cancelled request is not a storage answer: the error is re-raised.
	objects := &personaPictureObjects{putErr: context.Canceled}
	writes := personaPictureWrites(objects, true)
	var invalid *PersonaPictureInvalid
	if _, err := writes.uploadPicture(context.Background(), []byte("picture"), "a1000000-0000-4000-8000-000000000001"); !errors.Is(err, context.Canceled) ||
		errors.As(err, &invalid) {
		t.Fatalf("cancelled request: %v", err)
	}
}
