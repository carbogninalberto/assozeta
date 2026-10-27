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
