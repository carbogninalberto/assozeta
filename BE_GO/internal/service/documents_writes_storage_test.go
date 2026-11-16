package service

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"net/http"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// documentsUploadStore is default_storage for one upload: BeginDocumentUpload
// answers the linked transaction, the other store methods are not reached.
type documentsUploadStore struct {
	domain.DocumentUploadStore
	tx domain.DocumentUploadTransaction
}

func (s documentsUploadStore) BeginDocumentUpload(context.Context) (domain.DocumentUploadTransaction, error) {
	return s.tx, nil
}

// documentsUploadObjects is default_storage's exists and put; GetObject is not
// part of an upload.
type documentsUploadObjects struct {
	existsErr error
	putErr    error
}

func (documentsUploadObjects) Location() string { return "tenant-files" }

func (o documentsUploadObjects) Exists(context.Context, string) (bool, error) {
	return false, o.existsErr
}

func (o documentsUploadObjects) Put(context.Context, string, []byte, string, string) error {
	return o.putErr
}

// documentsUploadTransaction records the document writes an upload makes; the
// other transaction methods are never reached by these tests.
type documentsUploadTransaction struct {
	domain.DocumentUploadTransaction
	created []domain.NewDocument
	deleted []domain.NewDocument
	commits int
}

func (t *documentsUploadTransaction) AssociationForOwner(context.Context, string, *string) (string, error) {
	return "b1000000-0000-4000-8000-000000000001", nil
}

func (t *documentsUploadTransaction) ActorAssociation(context.Context, string) (*string, error) {
	return nil, nil
}

func (t *documentsUploadTransaction) Subscription(context.Context, string, domain.MedicalTargetScope) (*domain.MedicalSubscription, error) {
	return &domain.MedicalSubscription{ID: "c1000000-0000-4000-8000-000000000001"}, nil
}

func (t *documentsUploadTransaction) CreateDocument(_ context.Context, document domain.NewDocument, _ *string,
	_ domain.LoginAuditContext, _ time.Time) error {
	t.created = append(t.created, document)
	return nil
}

func (t *documentsUploadTransaction) DeleteDocument(_ context.Context, document domain.NewDocument, _ string, _ *string,
	_ domain.LoginAuditContext, _ time.Time) error {
	t.deleted = append(t.deleted, document)
	return nil
}

func (t *documentsUploadTransaction) Commit() error {
	t.commits++
	return nil
}

func (t *documentsUploadTransaction) Rollback() error { return nil }

func documentsUploadWrites(objects domain.DocumentObjectStore, tx *documentsUploadTransaction) *DocumentUploads {
	return documentsUploadWritesWith(objects, tx, true)
}

func documentsUploadWritesWith(objects domain.DocumentObjectStore, tx *documentsUploadTransaction, overwrite bool) *DocumentUploads {
	now := func() time.Time { return time.Date(2026, 11, 16, 10, 0, 0, 123456000, time.UTC) }
	counter := 0
	ids := func() string {
		counter++
		return fmt.Sprintf("00000000-0000-4000-8000-%012d", counter)
	}
	return NewDocumentUploads(documentsUploadStore{tx: tx}, objects, nil, overwrite, now, ids)
}

func documentsUploadIdentity() domain.ResolvedIdentity {
	user := &domain.Account{ID: "d1000000-0000-4000-8000-000000000001", Role: domain.RoleAssociation}
	return domain.ResolvedIdentity{Actor: user, Effective: user, ScopeUser: user}
}

func documentsUploadBody() domain.RequestObject {
	return domain.RequestObject{{Name: "files", Value: []any{domain.RequestObject{
		{Name: "document", Value: "UmVnb2xhbWVudG8="}, {Name: "filename", Value: "regolamento.pdf"}}}}}
}

// documentsUploadMultipartBody is an item of multipart_threshold bytes, the
// content s3transfer splits into a multipart upload.
func documentsUploadMultipartBody() domain.RequestObject {
	content := base64.StdEncoding.EncodeToString(make([]byte, consumerStorageMultipartThreshold))
	return domain.RequestObject{{Name: "files", Value: []any{domain.RequestObject{
		{Name: "document", Value: content}, {Name: "filename", Value: "regolamento.pdf"}}}}}
}

// DocumentArchiveViewSet.add deletes the Document it created and answers its
// exception's final text, as it already did for a client error botocore raises
// at once.
func TestDocumentArchiveAddStorageFailures(t *testing.T) {
	for _, tc := range []struct {
		name string
		err  error
		want string
	}{
		{"client", storageResponseError("PutObject", http.StatusForbidden, consumerTestAPIError{"AccessDenied", "Access Denied."}),
			"An error occurred (AccessDenied) when calling the PutObject operation: Access Denied."},
		{"retried", storageResponseError("PutObject", http.StatusInternalServerError, consumerTestAPIError{"InternalError", "We encountered an internal error."}),
			"An error occurred (InternalError) when calling the PutObject operation (reached max retries: 4): We encountered an internal error."},
	} {
		t.Run(tc.name, func(t *testing.T) {
			tx := &documentsUploadTransaction{}
			uploads := documentsUploadWrites(documentsUploadObjects{putErr: tc.err}, tx)
			_, err := uploads.Add(context.Background(), documentsUploadIdentity(), documentsUploadBody(), domain.LoginAuditContext{})
			var archive *ArchiveAddError
			if !errors.As(err, &archive) || archive.Message != tc.want {
				t.Fatalf("%v, want %q", err, tc.want)
			}
			// The Document row was created and then deleted, in one commit.
			if len(tx.created) != 1 || len(tx.deleted) != 1 || tx.deleted[0] != tx.created[0] || tx.commits != 1 {
				t.Fatalf("created=%+v deleted=%+v commits=%d", tx.created, tx.deleted, tx.commits)
			}
		})
	}
	// A cancelled request is not a storage answer: the Document stays deleted,
	// the error is re-raised.
	tx := &documentsUploadTransaction{}
	uploads := documentsUploadWrites(documentsUploadObjects{putErr: context.Canceled}, tx)
	_, err := uploads.Add(context.Background(), documentsUploadIdentity(), documentsUploadBody(), domain.LoginAuditContext{})
	var archive *ArchiveAddError
	if !errors.Is(err, context.Canceled) || errors.As(err, &archive) {
		t.Fatalf("cancelled request: %v", err)
	}
	if len(tx.created) != 1 || len(tx.deleted) != 1 || tx.commits != 1 {
		t.Fatalf("created=%+v deleted=%+v commits=%d", tx.created, tx.deleted, tx.commits)
	}
}

// The exists check before the save is one HeadObject: boto3 names that
// operation, not the multipart upload the content would take.
func TestDocumentArchiveAddExistsFailure(t *testing.T) {
	tx := &documentsUploadTransaction{}
	uploads := documentsUploadWritesWith(documentsUploadObjects{existsErr: storageResponseError("HeadObject", http.StatusServiceUnavailable, nil)},
		tx, false)
	_, err := uploads.Add(context.Background(), documentsUploadIdentity(), documentsUploadMultipartBody(), domain.LoginAuditContext{})
	var archive *ArchiveAddError
	if !errors.As(err, &archive) ||
		archive.Message != "An error occurred (503) when calling the HeadObject operation (reached max retries: 4): Service Unavailable" {
		t.Fatalf("%v", err)
	}
	if len(tx.created) != 1 || len(tx.deleted) != 1 || tx.commits != 1 {
		t.Fatalf("created=%+v deleted=%+v commits=%d", tx.created, tx.deleted, tx.commits)
	}
}

// subscription_medical_certificate_upload answers 500 {'error': str(e)} with
// botocore's final text; the Document row is kept (the save raises after it).
func TestMedicalUploadStorageFailures(t *testing.T) {
	body := domain.RequestObject{{Name: "medical_certificate", Value: domain.RequestFile{Name: "certificato.pdf",
		Content: []byte("certificate")}}}
	for _, tc := range []struct {
		name string
		err  error
		want string
	}{
		{"client", storageResponseError("PutObject", http.StatusForbidden, consumerTestAPIError{"AccessDenied", "Access Denied."}),
			"An error occurred (AccessDenied) when calling the PutObject operation: Access Denied."},
		{"server", storageResponseError("PutObject", http.StatusInternalServerError, consumerTestAPIError{"InternalError", "We encountered an internal error."}),
			"An error occurred (InternalError) when calling the PutObject operation (reached max retries: 4): We encountered an internal error."},
	} {
		t.Run(tc.name, func(t *testing.T) {
			tx := &documentsUploadTransaction{}
			uploads := documentsUploadWrites(documentsUploadObjects{putErr: tc.err}, tx)
			_, err := uploads.MedicalUpload(context.Background(), documentsUploadIdentity(),
				"c1000000-0000-4000-8000-000000000001", "", body, domain.LoginAuditContext{})
			var medical *MedicalUploadError
			if !errors.As(err, &medical) || medical.Status != 500 || medical.Message != tc.want {
				t.Fatalf("%v, want 500 %q", err, tc.want)
			}
			// default_storage.save raises after the Document row was committed.
			if len(tx.created) != 1 || len(tx.deleted) != 0 || tx.commits != 1 {
				t.Fatalf("created=%+v deleted=%+v commits=%d", tx.created, tx.deleted, tx.commits)
			}
		})
	}
	// A cancelled request is not a storage answer.
	tx := &documentsUploadTransaction{}
	uploads := documentsUploadWrites(documentsUploadObjects{putErr: context.Canceled}, tx)
	_, err := uploads.MedicalUpload(context.Background(), documentsUploadIdentity(),
		"c1000000-0000-4000-8000-000000000001", "", body, domain.LoginAuditContext{})
	var medical *MedicalUploadError
	if !errors.Is(err, context.Canceled) || errors.As(err, &medical) {
		t.Fatalf("cancelled request: %v", err)
	}
}
