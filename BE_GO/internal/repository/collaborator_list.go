package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

type CollaboratorList struct{ db sqlx.QueryerContext }

func NewCollaboratorList(db sqlx.QueryerContext) *CollaboratorList { return &CollaboratorList{db} }

var _ domain.CollaboratorReader = (*CollaboratorList)(nil)

const collaboratorProjection = `SELECT user_id AS id,first_name AS firstname,last_name AS lastname,email,phone,role,connected_user_id AS connecteduser,collaborator_role AS collaboratorrole,collaborator_permissions AS permissions FROM bakney_user WHERE connected_user_id=$1 AND NOT deleted`

// No ordering/filter/pagination is added: Django's two querysets have no ordering.
func (r *CollaboratorList) Collaborators(ctx context.Context, owner uuid.UUID) ([]domain.CollaboratorRecord, error) {
	rows := []domain.CollaboratorRecord{}
	err := sqlx.SelectContext(ctx, r.db, &rows, collaboratorProjection, owner)
	if err != nil {
		return nil, err
	}
	return rows, nil
}
func (r *CollaboratorList) Collaborator(ctx context.Context, owner, id uuid.UUID) (*domain.CollaboratorRecord, error) {
	var row domain.CollaboratorRecord
	err := sqlx.GetContext(ctx, r.db, &row, collaboratorProjection+` AND user_id=$2 LIMIT 1`, owner, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}
func (r *CollaboratorList) CollaborationInvites(ctx context.Context, owner uuid.UUID) ([]domain.CollaborationInviteRecord, error) {
	rows := []domain.CollaborationInviteRecord{}
	err := sqlx.SelectContext(ctx, r.db, &rows, `SELECT collaboration_invite_id AS id,email,expiration_date AS expires,accepted,token,collaborator_role AS collaboratorrole,collaborator_permissions AS permissions FROM application_collaborationinvites WHERE user_id=$1`, owner)
	if err != nil {
		return nil, err
	}
	return rows, nil
}
