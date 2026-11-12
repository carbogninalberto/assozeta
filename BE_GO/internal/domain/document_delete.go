package domain

import "context"

// DocumentDeleteStore is application.document_scope.delete_document after its
// UUID check: found is false for a missing document, a user without an active
// association and a document that does not belong to that association (all
// "Document not found."); otherwise the document is deleted with its cascade
// and audit entries. group is the request's X-Group-Id (nil when absent or not
// a UUID), read by the delete receivers' group-aware managers.
type DocumentDeleteStore interface {
	Delete(ctx context.Context, document, user string, audit LoginAuditContext, group *string) (found bool, err error)
}
