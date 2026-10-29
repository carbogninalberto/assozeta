package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type Folders struct{ db *sqlx.DB }

func NewFolders(db *sqlx.DB) *Folders { return &Folders{db: db} }

var _ domain.FolderStore = (*Folders)(nil)

const folderColumns = `id, name, parent_id, sport_association_id, created_at, level, tree_id, lft, rght`

func (r *Folders) ActiveAssociationForUser(ctx context.Context, user string) (string, error) {
	return messageReadsAssociation(ctx, r.db, user)
}

func (r *Folders) AssociationFolders(ctx context.Context, association string) ([]domain.Folder, error) {
	rows := []domain.Folder{}
	err := r.db.SelectContext(ctx, &rows, `SELECT `+folderColumns+` FROM application_folder
 WHERE sport_association_id=$1 ORDER BY tree_id, lft`, association)
	return rows, err
}

func (r *Folders) FolderByID(ctx context.Context, id int64) (domain.Folder, error) {
	var row domain.Folder
	err := r.db.GetContext(ctx, &row, `SELECT `+folderColumns+` FROM application_folder WHERE id=$1`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return row, domain.ErrFolderParentNotFound
	}
	return row, err
}

func (r *Folders) Ancestors(ctx context.Context, folder domain.Folder) ([]domain.Folder, error) {
	rows := []domain.Folder{}
	err := r.db.SelectContext(ctx, &rows, `SELECT `+folderColumns+` FROM application_folder
 WHERE tree_id=$1 AND lft<=$2 AND rght>=$3 ORDER BY lft`, folder.TreeID, folder.Left, folder.Right)
	return rows, err
}

var _ domain.FolderDeleteStore = (*Folders)(nil)

func (r *Folders) BeginFolderDelete(ctx context.Context) (domain.FolderDeleteTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &folderDeleteTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}, nil
}

type folderDeleteTransaction struct{ *paymentCategoryTransaction }

// DeleteSubtree is MPTTModel.delete: _close_gap(width, rght, tree_id) on the
// refreshed node, then the collector's SET_NULL of archive rows and the
// cascading delete of the subtree. None of these run model saves.
func (t *folderDeleteTransaction) DeleteSubtree(ctx context.Context, id int64, association string) error {
	var node domain.Folder
	err := t.tx.GetContext(ctx, &node, `SELECT `+folderColumns+` FROM application_folder
 WHERE id=$1 AND sport_association_id=$2 FOR UPDATE`, id, association)
	if errors.Is(err, sql.ErrNoRows) {
		return domain.ErrFolderNotFound
	}
	if err != nil {
		return err
	}
	// Lock the whole tree before renumbering it.
	if _, err = t.tx.ExecContext(ctx, `SELECT 1 FROM application_folder WHERE tree_id=$1 FOR UPDATE`, node.TreeID); err != nil {
		return err
	}
	// Collect the subtree first: closing the gap shifts later siblings into
	// the deleted node's former (lft, rght) range.
	subtree := []int64{}
	if err = t.tx.SelectContext(ctx, &subtree, `SELECT id FROM application_folder WHERE tree_id=$1 AND lft>=$2 AND rght<=$3`,
		node.TreeID, node.Left, node.Right); err != nil {
		return err
	}
	width := node.Right - node.Left + 1
	if _, err = t.tx.ExecContext(ctx, `UPDATE application_folder SET
 lft=CASE WHEN lft>$2 THEN lft-$3 ELSE lft END, rght=CASE WHEN rght>$2 THEN rght-$3 ELSE rght END
 WHERE tree_id=$1 AND (lft>$2 OR rght>$2)`, node.TreeID, node.Right, width); err != nil {
		return err
	}
	if _, err = t.tx.ExecContext(ctx, `UPDATE application_sportassociationdocumentsarchive SET folder_id=NULL
 WHERE folder_id=ANY($1::int[])`, subtree); err != nil {
		return err
	}
	_, err = t.tx.ExecContext(ctx, `DELETE FROM application_folder WHERE id=ANY($1::int[])`, subtree)
	return err
}

var _ domain.FolderMoveStore = (*Folders)(nil)

func (r *Folders) BeginFolderMove(ctx context.Context) (domain.FolderMoveTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &folderDeleteTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}, nil
}

// MoveFolder ports django-mptt TreeManager._move_node for move_to(target)
// ('first-child') and move_to(None), then the node's save of parent_id.
func (t *folderDeleteTransaction) MoveFolder(ctx context.Context, id int64, association string, targetID *int64) error {
	var node domain.Folder
	err := t.tx.GetContext(ctx, &node, `SELECT `+folderColumns+` FROM application_folder
 WHERE id=$1 AND sport_association_id=$2`, id, association)
	if errors.Is(err, sql.ErrNoRows) {
		return domain.ErrFolderNotFound
	}
	if err != nil {
		return err
	}
	var target *domain.Folder
	if targetID != nil {
		var row domain.Folder
		err = t.tx.GetContext(ctx, &row, `SELECT `+folderColumns+` FROM application_folder WHERE id=$1`, *targetID)
		if errors.Is(err, sql.ErrNoRows) {
			return domain.ErrFolderTargetMissing
		}
		if err != nil {
			return err
		}
		if row.AssociationID != association {
			return domain.ErrFolderTargetForeign
		}
		target = &row
	}
	// Serialize concurrent tree changes for the involved trees.
	if _, err = t.tx.ExecContext(ctx, `LOCK TABLE application_folder IN SHARE ROW EXCLUSIVE MODE`); err != nil {
		return err
	}
	exec := func(query string, args ...any) error {
		_, err := t.tx.ExecContext(ctx, query, args...)
		return err
	}
	manageSpace := func(size, at, tree int64) error {
		return exec(`UPDATE application_folder SET lft=CASE WHEN lft>$1 THEN lft+$2 ELSE lft END,
 rght=CASE WHEN rght>$1 THEN rght+$2 ELSE rght END WHERE tree_id=$3 AND (lft>$1 OR rght>$1)`, at, size, tree)
	}
	interTreeMove := func(levelChange, leftRightChange, newTree int64) error {
		gap, gapLeft := node.Right-node.Left+1, node.Left-1
		return exec(`UPDATE application_folder SET
 level=CASE WHEN lft>=$1 AND lft<=$2 THEN level-$3 ELSE level END,
 tree_id=CASE WHEN lft>=$1 AND lft<=$2 THEN $4 ELSE tree_id END,
 lft=CASE WHEN lft>=$1 AND lft<=$2 THEN lft-$5 WHEN lft>$6 THEN lft-$7 ELSE lft END,
 rght=CASE WHEN rght>=$1 AND rght<=$2 THEN rght-$5 WHEN rght>$6 THEN rght-$7 ELSE rght END
 WHERE tree_id=$8`, node.Left, node.Right, levelChange, newTree, leftRightChange, gapLeft, gap, node.TreeID)
	}
	var parent *int64
	switch {
	case target == nil:
		if node.ParentID != nil {
			// _make_child_root_node with the next tree id over all folders.
			var next int64
			if err = t.tx.GetContext(ctx, &next, `SELECT COALESCE(MAX(tree_id), 0)+1 FROM application_folder`); err != nil {
				return err
			}
			if err = interTreeMove(int64(node.Level), node.Left-1, next); err != nil {
				return err
			}
		}
	case node.ParentID == nil:
		// _move_root_node into the target's tree as its first child.
		if node.ID == target.ID || node.TreeID == target.TreeID {
			return domain.ErrFolderInvalidMove
		}
		width := node.Right - node.Left + 1
		levelChange := int64(node.Level - target.Level - 1)
		leftRightChange := node.Left - target.Left - 1
		if err = manageSpace(width, target.Left, target.TreeID); err != nil {
			return err
		}
		if err = exec(`UPDATE application_folder SET level=level-$1, lft=lft-$2, rght=rght-$2, tree_id=$3
 WHERE lft>=$4 AND lft<=$5 AND tree_id=$6`, levelChange, leftRightChange, target.TreeID, node.Left, node.Right, node.TreeID); err != nil {
			return err
		}
		parent = &target.ID
	case node.TreeID != target.TreeID:
		// _move_child_to_new_tree.
		width := node.Right - node.Left + 1
		if err = manageSpace(width, target.Left, target.TreeID); err != nil {
			return err
		}
		if err = interTreeMove(int64(node.Level-target.Level-1), node.Left-target.Left-1, target.TreeID); err != nil {
			return err
		}
		parent = &target.ID
	default:
		// _move_child_within_tree as first child.
		if node.ID == target.ID || (node.Left < target.Left && target.Left < node.Right) {
			return domain.ErrFolderInvalidMove
		}
		width := node.Right - node.Left + 1
		newLeft, newRight := target.Left+1, target.Left+width
		if target.Left > node.Left {
			newLeft, newRight = target.Left-width+1, target.Left
		}
		levelChange := int64(node.Level - target.Level - 1)
		leftBoundary, rightBoundary := min(node.Left, newLeft), max(node.Right, newRight)
		leftRightChange := newLeft - node.Left
		gap := width
		if leftRightChange > 0 {
			gap = -gap
		}
		if err = exec(`UPDATE application_folder SET
 level=CASE WHEN lft>=$1 AND lft<=$2 THEN level-$3 ELSE level END,
 lft=CASE WHEN lft>=$1 AND lft<=$2 THEN lft+$4 WHEN lft>=$5 AND lft<=$6 THEN lft+$7 ELSE lft END,
 rght=CASE WHEN rght>=$1 AND rght<=$2 THEN rght+$4 WHEN rght>=$5 AND rght<=$6 THEN rght+$7 ELSE rght END
 WHERE tree_id=$8`, node.Left, node.Right, levelChange, leftRightChange, leftBoundary, rightBoundary, gap, node.TreeID); err != nil {
			return err
		}
		parent = &target.ID
	}
	// node.save(): the raw updates leave parent_id to the model save.
	return exec(`UPDATE application_folder SET parent_id=$2 WHERE id=$1`, node.ID, parent)
}

func (t *folderDeleteTransaction) OwnFolder(ctx context.Context, id int64, association string) error {
	var exists bool
	if err := t.tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_folder WHERE id=$1 AND sport_association_id=$2)`,
		id, association); err != nil {
		return err
	}
	if !exists {
		return domain.ErrFolderNotFound
	}
	return nil
}
