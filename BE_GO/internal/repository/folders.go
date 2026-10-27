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
