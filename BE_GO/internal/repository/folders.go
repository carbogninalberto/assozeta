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
