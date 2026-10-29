package domain

import (
	"context"
	"errors"
	"time"
)

var (
	ErrFolderParentNotFound = errors.New("Parent folder not found.")
	ErrFolderForeign        = errors.New("Cannot access folder from different sport association.")
)

// Folder is one MPTT node.
type Folder struct {
	ID            int64     `db:"id"`
	Name          string    `db:"name"`
	ParentID      *int64    `db:"parent_id"`
	AssociationID string    `db:"sport_association_id"`
	CreatedAt     time.Time `db:"created_at"`
	Level         int       `db:"level"`
	TreeID        int64     `db:"tree_id"`
	Left          int64     `db:"lft"`
	Right         int64     `db:"rght"`
}

type FolderStore interface {
	ActiveAssociationForUser(ctx context.Context, user string) (string, error)
	// AssociationFolders are in tree order (tree_id, lft).
	AssociationFolders(ctx context.Context, association string) ([]Folder, error)
	FolderByID(ctx context.Context, id int64) (Folder, error)
	// Ancestors include the folder itself, root first.
	Ancestors(ctx context.Context, folder Folder) ([]Folder, error)
}

// ErrFolderNotFound is get_object's model 404 on the association's folders.
var ErrFolderNotFound = errors.New("No Folder matches the given query.")

type FolderDeleteStore interface {
	BeginFolderDelete(context.Context) (FolderDeleteTransaction, error)
}

type FolderDeleteTransaction interface {
	AssociationForOwner(context.Context, string, *string) (string, error)
	// DeleteSubtree closes the MPTT gap, detaches archived documents and
	// deletes the folder with its descendants.
	DeleteSubtree(ctx context.Context, id int64, association string) error
	Commit() error
	Rollback() error
}

var (
	// ErrFolderInvalidMove is mptt InvalidMove (into itself or a descendant), raised before any write (500).
	ErrFolderInvalidMove = errors.New("invalid folder move")
	// ErrFolderTargetMissing is Folder.objects.get(id=new_parent) raising DoesNotExist (500).
	ErrFolderTargetMissing = errors.New("folder move target does not exist")
	ErrFolderTargetForeign = errors.New("Cannot move to folder from different sport association.")
)

type FolderMoveTransaction interface {
	FolderDeleteTransaction
	// OwnFolder is get_object on the association's folders.
	OwnFolder(ctx context.Context, id int64, association string) error
	// MoveFolder is move_to(target, 'first-child'), or move_to(None) for a nil target.
	MoveFolder(ctx context.Context, id int64, association string, target *int64) error
}

type FolderMoveStore interface {
	BeginFolderMove(context.Context) (FolderMoveTransaction, error)
}
