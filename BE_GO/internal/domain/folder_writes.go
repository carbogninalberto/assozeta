package domain

import (
	"context"
	"errors"
	"time"
)

var (
	// ErrFolderForeignReference is tenant_scope's generic PermissionDenied for a
	// parent or sport_association that belongs to another association.
	ErrFolderForeignReference = errors.New("folder reference of another association")
	// ErrFolderAddBody is FolderViewSet.add's request.data['sport_association']
	// assignment on a body that is not a mutable dict (TypeError or an
	// immutable QueryDict): a 500.
	ErrFolderAddBody = errors.New("folder add body is not a mutable dict")
	// ErrFolderWriteNotPorted refuses an input whose Django handling is not
	// ported (a 500 before any write).
	ErrFolderWriteNotPorted = errors.New("folder write input not ported")
	// ErrFolderPKOverflow is int(float('inf')) on a pk value: OverflowError, a 500.
	ErrFolderPKOverflow = errors.New("folder pk overflow")
)

// FolderWriteTransaction serves FolderViewSet.add and update in one owned
// transaction; tree changes run under a table lock.
type FolderWriteTransaction interface {
	AssociationForOwner(ctx context.Context, owner string, impersonationAssociation *string) (string, error)
	AssociationExists(ctx context.Context, id string) (bool, error)
	// OwnedFolder is get_object() on the association's folders (ErrFolderNotFound).
	OwnedFolder(ctx context.Context, id int64, association string) (Folder, error)
	// FindFolder is the parent field's lookup over every folder; nil when missing.
	FindFolder(ctx context.Context, id int64) (*Folder, error)
	// FolderNameTaken is UniqueTogetherValidator's query (name, parent, sport_association),
	// excluding the updated folder; a nil parent matches IS NULL.
	FolderNameTaken(ctx context.Context, name string, parent *int64, association string, exclude *int64) (bool, error)
	// InsertFolder is MPTT insert_node as the parent's last child, or a new
	// tree with MAX(tree_id)+1 for a nil parent, then the INSERT.
	InsertFolder(ctx context.Context, name string, parent *int64, association string, createdAt time.Time) (Folder, error)
	// SaveFolder is MPTTModel.save of an existing node: a changed parent moves
	// it ('last-child', or a new root), then its fields are stored.
	SaveFolder(ctx context.Context, id int64, name string, parent *int64, parentChanged bool, association string, createdAt time.Time) (Folder, error)
	// Descendants are the folder's subtree below it, in tree order.
	Descendants(ctx context.Context, folder Folder) ([]Folder, error)
	Commit() error
	Rollback() error
}

type FolderWriteStore interface {
	BeginFolderWrite(context.Context) (FolderWriteTransaction, error)
}
