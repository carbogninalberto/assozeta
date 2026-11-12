package domain

import (
	"context"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ErrDocmanagerNotPorted refuses a docmanager path that renders a PDF (Puppeteer
// and the Celery storage task), deletes a document, or depends on behaviour this
// port does not reproduce. It is answered with the 500 page before any write.
var ErrDocmanagerNotPorted = refusal.New("docmanager path not ported")

// StoredDocument is the docmanager.Document row a retrieval streams.
type StoredDocument struct {
	ID       string    `db:"document_id"`
	Filename string    `db:"filename"`
	Filepath *string   `db:"filepath"`
	Created  time.Time `db:"creation_date"`
}

// DocmanagerReadsStore reads Documents and the rows that reference them. A nil
// document means no matching row. group is the X-Group-Id GroupAwareManager
// filter (nil when absent or not a UUID).
type DocmanagerReadsStore interface {
	MemberDocumentStore
	Document(ctx context.Context, id string) (*StoredDocument, error)
	DocumentWithToken(ctx context.Context, id, token string) (*StoredDocument, error)
	SubscriptionDocument(ctx context.Context, subscription string, group *string) (*StoredDocument, error)
	MedicalDocument(ctx context.Context, medical string) (*StoredDocument, error)
	InvoiceDocument(ctx context.Context, invoice string, group *string) (*StoredDocument, error)
	ExportArchiveDocument(ctx context.Context, association, document string) (*StoredDocument, error)
	FirstArchiveOwner(ctx context.Context, document string) (owner string, found bool, err error)
	VisibleDocument(ctx context.Context, scope ResourceScope, document string) (bool, error)
	RenderSource(ctx context.Context, document string, group *string) (string, error)
	HasActiveAssociation(ctx context.Context, user string) (bool, error)
}
