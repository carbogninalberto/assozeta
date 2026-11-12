package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type MessageMutations struct{ db *sqlx.DB }

func NewMessageMutations(db *sqlx.DB) *MessageMutations { return &MessageMutations{db: db} }

var _ domain.MessageMutationStore = (*MessageMutations)(nil)

func (r *MessageMutations) BeginMessageMutations(ctx context.Context) (domain.MessageMutationTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &messageMutationTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}, nil
}

// Reuse the concrete shared owner/association lock and owned transaction
// implementation, including the restore advisory barrier and cleanup join.
type messageMutationTransaction struct{ *paymentCategoryTransaction }

var _ domain.MessageMutationTransaction = (*messageMutationTransaction)(nil)

func (t *messageMutationTransaction) OwnerForUpdate(ctx context.Context, id string) (int, bool, bool, error) {
	role, active, deleted, err := t.paymentCategoryTransaction.OwnerForUpdate(ctx, id)
	if errors.Is(err, domain.ErrPaymentCategoryForbidden) {
		err = domain.ErrMessageMutationForbidden
	}
	return role, active, deleted, err
}
func (t *messageMutationTransaction) MessageForUpdate(ctx context.Context, id, association string) (domain.MessageMutationState, error) {
	var state domain.MessageMutationState
	err := t.tx.GetContext(ctx, &state, `SELECT message_id,sport_association_id,type,message,subject,created_at FROM communications_message WHERE message_id=$1 AND sport_association_id=$2 FOR UPDATE`, id, association)
	if errors.Is(err, sql.ErrNoRows) {
		err = domain.ErrMessageMutationNotFound
	}
	return state, err
}
func (t *messageMutationTransaction) InsertMessage(ctx context.Context, state domain.MessageMutationState) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO communications_message(message_id,sport_association_id,type,message,subject,created_at) VALUES($1,$2,$3,$4,$5,$6)`, state.ID, state.AssociationID, state.Type, state.Message, state.Subject, state.CreatedAt)
	return err
}
func (t *messageMutationTransaction) DeleteMessageDeliveries(ctx context.Context, id string) error {
	_, err := t.tx.ExecContext(ctx, `DELETE FROM communications_messagetransaction WHERE message_id=$1`, id)
	return err
}
func (t *messageMutationTransaction) DeleteMessage(ctx context.Context, id, association string) error {
	result, err := t.tx.ExecContext(ctx, `DELETE FROM communications_message WHERE message_id=$1 AND sport_association_id=$2`, id, association)
	if err != nil {
		return err
	}
	count, err := result.RowsAffected()
	if err == nil && count != 1 {
		return domain.ErrMessageMutationNotFound
	}
	return err
}
func (t *messageMutationTransaction) AppendMessageAudit(ctx context.Context, event domain.MessageMutationAudit) error {
	return appendModelAuditForApp(ctx, t.tx, "communications", modelAuditEntry{Model: "message", Key: event.ID, Representation: "Message object (" + event.ID + ")", Action: event.Action, Changes: event.Changes, Context: event.Context, AssociationID: &event.AssociationID, ResolutionPath: &event.ResolutionPath, At: event.At})
}
