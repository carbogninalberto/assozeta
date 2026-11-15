package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/documentkey"
)

// membershipDocumentTransaction records the document writes handleDocuments
// makes; the other transaction methods are never reached by these tests.
type membershipDocumentTransaction struct {
	domain.SubscriptionMembershipTransaction
	created []domain.NewDocument
	linked  []string
}

func (f *membershipDocumentTransaction) CreateMembershipDocument(ctx context.Context, document domain.NewDocument,
	audit domain.LoginAuditContext, at time.Time) error {
	f.created = append(f.created, document)
	return nil
}

func (f *membershipDocumentTransaction) LinkMembershipDocument(ctx context.Context, membership, document string) error {
	f.linked = append(f.linked, document)
	return nil
}

// subscriptionMembershipDocumentWriter is the writer the document tests walk
// with: the document storage with no stores behind it (a row that would be
// stored reflects a behaviour these tests do not exercise).
func subscriptionMembershipDocumentWriter() (*SubscriptionMemberships, *membershipDocumentTransaction) {
	now := func() time.Time { return time.Date(2026, 11, 15, 10, 0, 0, 123456000, time.UTC) }
	counter := 0
	ids := func() string {
		counter++
		return fmt.Sprintf("00000000-0000-4000-8000-%012d", counter)
	}
	uploads := NewDocumentUploads(nil, nil, nil, false, now, ids)
	return NewSubscriptionMemberships(nil, now, ids).WithUploads(uploads), &membershipDocumentTransaction{}
}

// membershipDocumentItem is one documents_data item with a filename value.
func membershipDocumentItem(filename any) domain.RequestObject {
	return domain.RequestObject{{Name: "document", Value: "UmVnb2xhbWVudG8="}, {Name: "filename", Value: filename}}
}

// A filename that is not a str is stored as str(value) and its create entry is
// written, then os.path.join raises: the row stays, nothing is stored or
// linked (no fake implements saving).
func TestSubscriptionMembershipDocumentsNumericFilenameKeepsRow(t *testing.T) {
	memberships, tx := subscriptionMembershipDocumentWriter()
	added, err := memberships.handleDocuments(context.Background(), tx, "membership", dto.SubscriptionMembershipWrite{
		Documents: []domain.RequestObject{membershipDocumentItem(json.Number("123"))},
	}, domain.LoginAuditContext{})
	if added != 0 || !errors.Is(err, errSubscriptionMembershipDocumentStore) {
		t.Fatalf("added=%d err=%v", added, err)
	}
	if len(tx.created) != 1 || tx.created[0].Filename != "123" || len(tx.linked) != 0 {
		t.Fatalf("created=%+v linked=%v", tx.created, tx.linked)
	}
}

// A None filename fails the INSERT: no Document row, the 500.
func TestSubscriptionMembershipDocumentsNoneFilenameKeepsNoRow(t *testing.T) {
	memberships, tx := subscriptionMembershipDocumentWriter()
	added, err := memberships.handleDocuments(context.Background(), tx, "membership", dto.SubscriptionMembershipWrite{
		Documents: []domain.RequestObject{membershipDocumentItem(nil)},
	}, domain.LoginAuditContext{})
	if added != 0 || !errors.Is(err, ErrSubscriptionMembershipDocumentDecode) || len(tx.created) != 0 {
		t.Fatalf("added=%d err=%v created=%+v", added, err, tx.created)
	}
}

// A name Storage.save rejects is written by the INSERT, then raises a
// SuspiciousFileOperation (the 400 page): the row stays, nothing is stored or
// linked.
func TestSubscriptionMembershipDocumentsTraversalFilenameKeepsRow(t *testing.T) {
	memberships, tx := subscriptionMembershipDocumentWriter()
	added, err := memberships.handleDocuments(context.Background(), tx, "membership", dto.SubscriptionMembershipWrite{
		Documents: []domain.RequestObject{membershipDocumentItem("../x.pdf")},
	}, domain.LoginAuditContext{})
	var suspicious *documentkey.SuspiciousFileError
	if added != 0 || !errors.As(err, &suspicious) {
		t.Fatalf("added=%d err=%v", added, err)
	}
	if len(tx.created) != 1 || tx.created[0].Filename != "../x.pdf" || len(tx.linked) != 0 {
		t.Fatalf("created=%+v linked=%v", tx.created, tx.linked)
	}
}

// A 256-character name overflows varchar(255): no Document row, the 500.
func TestSubscriptionMembershipDocumentsLongFilenameKeepsNoRow(t *testing.T) {
	memberships, tx := subscriptionMembershipDocumentWriter()
	added, err := memberships.handleDocuments(context.Background(), tx, "membership", dto.SubscriptionMembershipWrite{
		Documents: []domain.RequestObject{membershipDocumentItem(strings.Repeat("a", 256))},
	}, domain.LoginAuditContext{})
	if added != 0 || !errors.Is(err, ErrSubscriptionMembershipDocumentDecode) || len(tx.created) != 0 {
		t.Fatalf("added=%d err=%v created=%+v", added, err, tx.created)
	}
}

// A multipart file part is a value that is not a str: its str() is the file's
// name, which the row keeps, then os.path.join raises.

func TestSubscriptionMembershipDocumentsFilePartFilenameKeepsRow(t *testing.T) {
	memberships, tx := subscriptionMembershipDocumentWriter()
	added, err := memberships.handleDocuments(context.Background(), tx, "membership", dto.SubscriptionMembershipWrite{
		Documents: []domain.RequestObject{
			membershipDocumentItem(domain.RequestFile{Name: "regolamento.pdf"}),
		},
	}, domain.LoginAuditContext{})
	// A multipart file part is stored as its name, then os.path.join raises.
	if added != 0 || !errors.Is(err, errSubscriptionMembershipDocumentStore) || len(tx.created) != 1 ||
		tx.created[0].Filename != "regolamento.pdf" || len(tx.linked) != 0 {
		t.Fatalf("added=%d err=%v created=%+v linked=%v", added, err, tx.created, tx.linked)
	}
}
