package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type CollaboratorDeletes struct {
	db  *sqlx.DB
	now func() time.Time
}

func NewCollaboratorDeletes(db *sqlx.DB, now func() time.Time) *CollaboratorDeletes {
	return &CollaboratorDeletes{db: db, now: now}
}

var _ domain.CollaboratorDeleteStore = (*CollaboratorDeletes)(nil)

func (r *CollaboratorDeletes) BeginCollaboratorDelete(ctx context.Context, audit domain.LoginAuditContext, group *string) (domain.CollaboratorDeleteTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &collaboratorDeleteTransaction{ownedTransaction: tx,
		run: &collaboratorDeleteRun{tx: tx.tx, graph: collaboratorDeleteGraphLoaded, audit: audit, group: group, now: r.now}}, nil
}

type collaboratorDeleteTransaction struct {
	*ownedTransaction
	run        *collaboratorDeleteRun
	invitation *collaboratorDeleteRow
}

var _ domain.CollaboratorDeleteTransaction = (*collaboratorDeleteTransaction)(nil)

func (t *collaboratorDeleteTransaction) LockOwner(ctx context.Context, owner string) error {
	var id string
	err := t.tx.GetContext(ctx, &id, `SELECT user_id::text FROM bakney_user WHERE user_id=$1::uuid AND NOT deleted FOR UPDATE`, owner)
	if errors.Is(err, sql.ErrNoRows) {
		return errors.New("collaborators_delete: request user does not exist")
	}
	return err
}

func (t *collaboratorDeleteTransaction) lockedUser(ctx context.Context, where string, args ...any) ([]*collaboratorDeleteRow, error) {
	return t.run.load(ctx, t.run.graph.Models[collaboratorDeleteUser], where+` AND NOT t.deleted ORDER BY t.user_id LIMIT 1 FOR UPDATE`, args...)
}

func (t *collaboratorDeleteTransaction) DeleteConnectedUser(ctx context.Context, owner, id string) (bool, error) {
	rows, err := t.lockedUser(ctx, `t.connected_user_id=$1::uuid AND t.user_id=$2::uuid`, owner, id)
	if err != nil || len(rows) == 0 {
		return false, err
	}
	return true, t.run.Delete(ctx, collaboratorDeleteUser, rows)
}

func (t *collaboratorDeleteTransaction) Invitation(ctx context.Context, owner, id string) (*domain.CollaboratorDeleteInvitation, error) {
	rows, err := t.run.load(ctx, t.run.graph.Models[collaboratorDeleteInvite], `t.user_id=$1::uuid AND t.collaboration_invite_id=$2::uuid
 ORDER BY t.collaboration_invite_id LIMIT 1 FOR UPDATE`, owner, id)
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	t.invitation = rows[0]
	email, _ := rows[0].stored["email"].(string)
	return &domain.CollaboratorDeleteInvitation{Email: email, Accepted: rows[0].stored["accepted"] == true}, nil
}

func (t *collaboratorDeleteTransaction) DeleteAccountByEmail(ctx context.Context, owner, email string) error {
	rows, err := t.lockedUser(ctx, `t.email=$1 AND t.connected_user_id=$2::uuid`, email, owner)
	if err != nil {
		return err
	}
	return t.run.Delete(ctx, collaboratorDeleteUser, rows)
}

func (t *collaboratorDeleteTransaction) DeleteInvitation(ctx context.Context) error {
	return t.run.Delete(ctx, collaboratorDeleteInvite, []*collaboratorDeleteRow{t.invitation})
}
