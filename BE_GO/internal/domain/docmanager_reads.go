package domain

import (
	"context"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ErrDocmanagerUnreachable is a state no request or stored row produces (each
// site names the decoder or constraint that rules it out).
var ErrDocmanagerUnreachable = refusal.AppState("docmanager: unreachable state")

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
