package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

var _ domain.FolderWriteStore = (*Folders)(nil)

func (r *Folders) BeginFolderWrite(ctx context.Context) (domain.FolderWriteTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &folderDeleteTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}, nil
}

func (t *folderDeleteTransaction) folder(ctx context.Context, query string, args ...any) (*domain.Folder, error) {
	var row domain.Folder
	err := t.tx.GetContext(ctx, &row, `SELECT `+folderColumns+` FROM application_folder WHERE `+query, args...)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *folderDeleteTransaction) OwnedFolder(ctx context.Context, id int64, association string) (domain.Folder, error) {
	row, err := t.folder(ctx, `id=$1 AND sport_association_id=$2`, id, association)
	if err != nil {
		return domain.Folder{}, err
	}
	if row == nil {
		return domain.Folder{}, domain.ErrFolderNotFound
	}
	return *row, nil
}

func (t *folderDeleteTransaction) FindFolder(ctx context.Context, id int64) (*domain.Folder, error) {
	return t.folder(ctx, `id=$1`, id)
}

func (t *folderDeleteTransaction) FolderNameTaken(ctx context.Context, name string, parent *int64, association string, exclude *int64) (bool, error) {
	var taken bool
	err := t.tx.GetContext(ctx, &taken, `SELECT EXISTS(SELECT 1 FROM application_folder
 WHERE name=$1 AND parent_id IS NOT DISTINCT FROM $2::integer AND sport_association_id=$3 AND ($4::integer IS NULL OR id<>$4::integer))`,
		name, parent, association, exclude)
	return taken, err
}

func (t *folderDeleteTransaction) lockFolders(ctx context.Context) error {
	_, err := t.tx.ExecContext(ctx, `LOCK TABLE application_folder IN SHARE ROW EXCLUSIVE MODE`)
	return err
}

// InsertFolder is insert_at(parent, 'last-child'): for a parent, its
// refreshed rght opens a space of 2 before it (_create_space(2, rght-1)) and
// the node takes (rght, rght+1) at the next level; otherwise a new tree with
// MAX(tree_id)+1 over every folder.
func (t *folderDeleteTransaction) InsertFolder(ctx context.Context, name string, parentID *int64, association string,
	createdAt time.Time) (domain.Folder, error) {
	if err := t.lockFolders(ctx); err != nil {
		return domain.Folder{}, err
	}
	row := domain.Folder{Name: name, ParentID: parentID, AssociationID: association, CreatedAt: createdAt, Left: 1, Right: 2}
	if parentID == nil {
		if err := t.tx.GetContext(ctx, &row.TreeID, `SELECT COALESCE(MAX(tree_id), 0)+1 FROM application_folder`); err != nil {
			return domain.Folder{}, err
		}
	} else {
		parent, err := t.folder(ctx, `id=$1`, *parentID)
		if err != nil {
			return domain.Folder{}, err
		}
		if parent == nil {
			return domain.Folder{}, sql.ErrNoRows
		}
		if _, err = t.tx.ExecContext(ctx, `UPDATE application_folder SET lft=CASE WHEN lft>$1 THEN lft+2 ELSE lft END,
 rght=CASE WHEN rght>$1 THEN rght+2 ELSE rght END WHERE tree_id=$2 AND (lft>$1 OR rght>$1)`, parent.Right-1, parent.TreeID); err != nil {
			return domain.Folder{}, err
		}
		row.TreeID, row.Left, row.Right, row.Level = parent.TreeID, parent.Right, parent.Right+1, parent.Level+1
	}
	err := t.tx.GetContext(ctx, &row.ID, `INSERT INTO application_folder (name, parent_id, sport_association_id, created_at, lft, rght, tree_id, level)
 VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id`, row.Name, row.ParentID, row.AssociationID, row.CreatedAt, row.Left, row.Right,
		row.TreeID, row.Level)
	if err != nil {
		return domain.Folder{}, err
	}
	stored, err := t.folder(ctx, `id=$1`, row.ID)
	if err != nil {
		return domain.Folder{}, err
	}
	return *stored, nil
}

// SaveFolder is MPTTModel.save for an existing node. A changed parent moves
// the node as the new parent's last child (_move_node 'last-child'), or makes
// it a root after the rightmost root (the next tree id over every folder);
// InvalidMove precedes any write. Model.save then stores the fields (the
// unchanged-parent path saves the user fields only, with the same result).
func (t *folderDeleteTransaction) SaveFolder(ctx context.Context, id int64, name string, parentID *int64, parentChanged bool,
	association string, createdAt time.Time) (domain.Folder, error) {
	if err := t.lockFolders(ctx); err != nil {
		return domain.Folder{}, err
	}
	node, err := t.folder(ctx, `id=$1`, id)
	if err != nil {
		return domain.Folder{}, err
	}
	if node == nil {
		return domain.Folder{}, domain.ErrFolderNotFound
	}
	if parentChanged {
		var target *domain.Folder
		if parentID != nil {
			if target, err = t.folder(ctx, `id=$1`, *parentID); err != nil {
				return domain.Folder{}, err
			}
			if target == nil {
				return domain.Folder{}, sql.ErrNoRows
			}
		}
		if err = t.moveNode(ctx, *node, target, true); err != nil {
			return domain.Folder{}, err
		}
	}
	if _, err = t.tx.ExecContext(ctx, `UPDATE application_folder SET name=$2, parent_id=$3, sport_association_id=$4, created_at=$5 WHERE id=$1`,
		id, name, parentID, association, createdAt); err != nil {
		return domain.Folder{}, err
	}
	stored, err := t.folder(ctx, `id=$1`, id)
	if err != nil {
		return domain.Folder{}, err
	}
	return *stored, nil
}

func (t *folderDeleteTransaction) Descendants(ctx context.Context, folder domain.Folder) ([]domain.Folder, error) {
	rows := []domain.Folder{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT `+folderColumns+` FROM application_folder
 WHERE tree_id=$1 AND lft>$2 AND rght<$3 ORDER BY lft`, folder.TreeID, folder.Left, folder.Right)
	return rows, err
}
