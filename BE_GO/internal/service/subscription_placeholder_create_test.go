package service

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pilplaceholder"
)

// placeholderCreateTransaction is create_subscription's transaction where only
// the placeholder branch's two writes are exercised: the other methods stay
// unimplemented (a call on them panics, failing the test).
type placeholderCreateTransaction struct {
	domain.SubscriptionCreateTransaction
	createErr error
	created   []domain.NewDocument
	deleted   []domain.NewDocument
}

func (t *placeholderCreateTransaction) CreatePlaceholderDocument(_ context.Context, document domain.NewDocument, _ *string,
	_ domain.LoginAuditContext, _ time.Time) error {
	t.created = append(t.created, document)
	return t.createErr
}

func (t *placeholderCreateTransaction) DeletePlaceholderDocument(_ context.Context, document domain.NewDocument, _ *string,
	_ domain.LoginAuditContext, _ time.Time) error {
	t.deleted = append(t.deleted, document)
	return nil
}

func placeholderCreateData() (domain.RequestObject, domain.RequestObject) {
	data := domain.RequestObject{{Name: "medical_certificate", Value: domain.RequestObject{
		{Name: "medical_id", Value: nil},
		{Name: "certificate_expring_date", Value: "2027-03-31"},
	}}}
	associate := domain.RequestObject{{Name: "first_name", Value: "Pia"}, {Name: "last_name", Value: "Segnaposto"}}
	return data, associate
}

func placeholderCreateService() *SubscriptionCreates {
	now := func() time.Time { return time.Date(2026, time.November, 12, 9, 0, 0, 123456000, time.UTC) }
	// A nil object store is enough: the name validation runs before it, and the
	// tests that store successfully stop at the Document insert instead.
	return NewSubscriptionCreates(nil, nil, nil, now, nil).WithPlaceholders(
		NewSubscriptionPlaceholders(NewDocumentUploads(nil, nil, nil, false, now, nil), pilplaceholder.ZlibClassic))
}

// create_subscription's except block for a name Storage.save rejects: the
// Document the view created is deleted (its delete entry carries the row) and
// the registration goes on without a certificate; no error escapes.
func TestSubscriptionCreatePlaceholderRejectedNameDeletesDocument(t *testing.T) {
	tx := &placeholderCreateTransaction{}
	data, associate := placeholderCreateData()
	associate = objectSet(associate, "first_name", "x/../y") // medical_certificate_x/../y_Segnaposto.png
	request := SubscriptionCreateRequest{Audit: domain.LoginAuditContext{}}
	certificate, err := placeholderCreateService().createPlaceholder(context.Background(), tx, subscriptionAudit{}, data, associate,
		"owner", request)
	if err != nil || certificate != nil {
		t.Fatalf("createPlaceholder = %v, %v; want no certificate and no error", certificate, err)
	}
	if len(tx.created) != 1 || len(tx.deleted) != 1 {
		t.Fatalf("created %d documents, deleted %d; want one each", len(tx.created), len(tx.deleted))
	}
	if tx.created[0].ID == "" || tx.created[0].ID != tx.deleted[0].ID || tx.deleted[0].Filename != "medical_certificate_x/../y_Segnaposto.png" {
		t.Fatalf("deleted %+v, created %+v", tx.deleted[0], tx.created[0])
	}
}

// A Document.objects.create database error is not swallowed: the transaction is
// broken, so the view's except block cannot delete the row either and the whole
// registration rolls back (the 500 page).
func TestSubscriptionCreatePlaceholderDocumentDatabaseErrorPropagates(t *testing.T) {
	boom := errors.New("value too long for type character varying(255)")
	tx := &placeholderCreateTransaction{createErr: boom}
	data, associate := placeholderCreateData()
	if certificate, err := placeholderCreateService().createPlaceholder(context.Background(), tx, subscriptionAudit{}, data, associate,
		"owner", SubscriptionCreateRequest{}); !errors.Is(err, boom) || certificate != nil {
		t.Fatalf("createPlaceholder = %v, %v; want the insert error", certificate, err)
	}
	if len(tx.created) != 1 || len(tx.deleted) != 0 {
		t.Fatalf("created %d documents, deleted %d; want the failed insert only", len(tx.created), len(tx.deleted))
	}
}

// The file name and the drawn text take str() of the request's value: a number
// is its digits, as the view's f-string names it.
func TestSubscriptionCreatePlaceholderNumericName(t *testing.T) {
	tx := &placeholderCreateTransaction{createErr: errors.New("stop before storing")}
	data, associate := placeholderCreateData()
	associate = objectSet(associate, "first_name", json.Number("7"))
	if _, err := placeholderCreateService().createPlaceholder(context.Background(), tx, subscriptionAudit{}, data, associate,
		"owner", SubscriptionCreateRequest{}); err == nil {
		t.Fatal("createPlaceholder swallowed the insert error")
	}
	if len(tx.created) != 1 || tx.created[0].Filename != "medical_certificate_7_Segnaposto.png" {
		t.Fatalf("created %+v; want the numeric name", tx.created)
	}
}
