package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

type LoginPreparations struct{ db *sqlx.DB }

func NewLoginPreparations(db *sqlx.DB) *LoginPreparations { return &LoginPreparations{db: db} }
func (r *LoginPreparations) BeginLoginPreparation(ctx context.Context) (domain.LoginPreparationTransaction, error) {
	tx, err := r.db.BeginTxx(ctx, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &loginPreparationTransaction{tx: tx}, nil
}

type loginPreparationTransaction struct{ tx *sqlx.Tx }

func (t *loginPreparationTransaction) AccountForUpdate(ctx context.Context, id string) (domain.LoginChallenge, error) {
	var account domain.LoginChallenge
	err := t.tx.GetContext(ctx, &account, `SELECT `+accountColumns+`,first_name,last_name,two_fa,two_fa_secret FROM bakney_user WHERE user_id=$1 AND NOT deleted FOR UPDATE`, id)
	account.Password = ""
	if errors.Is(err, sql.ErrNoRows) {
		return account, domain.ErrAccountNotFound
	}
	return account, credentialTransactionError(err)
}
func (t *loginPreparationTransaction) SetUsername(ctx context.Context, id, value string) error {
	result, err := t.tx.ExecContext(ctx, `UPDATE bakney_user SET username=$1 WHERE user_id=$2`, value, id)
	if err != nil {
		return credentialTransactionError(err)
	}
	n, err := result.RowsAffected()
	if err == nil && n != 1 {
		return domain.ErrAccountNotFound
	}
	return credentialTransactionError(err)
}
func (t *loginPreparationTransaction) AuditAssociations(ctx context.Context, target string, actor *string) (map[string]string, error) {
	var rows []struct {
		UserID        string `db:"user_id"`
		AssociationID string `db:"sport_association_id"`
	}
	err := t.tx.SelectContext(ctx, &rows, `SELECT user_id,sport_association_id FROM application_sportassociation WHERE NOT deleted AND (user_id=$1 OR user_id=$2)`, target, actor)
	result := map[string]string{}
	for _, row := range rows {
		result[row.UserID] = row.AssociationID
	}
	return result, err
}
func (t *loginPreparationTransaction) AppendUsernameAudit(ctx context.Context, event domain.UsernameAudit) error {
	changes, err := json.Marshal(map[string][2]string{"username": {event.Before, event.After}})
	if err != nil {
		return credentialTransactionError(err)
	}
	var id int64
	err = t.tx.GetContext(ctx, &id, `INSERT INTO auditlog_logentry
 (content_type_id,object_pk,object_id,object_repr,serialized_data,action,changes_text,changes,
 actor_id,cid,remote_addr,remote_port,timestamp,additional_data,actor_email)
 SELECT id,$1,NULL,$2,NULL,1,'',$3::jsonb,$4::uuid,$5,$6::inet,$7,$8,NULL,$9
 FROM django_content_type WHERE app_label='application' AND model='user' RETURNING id`,
		event.AccountID, event.ObjectRepresentation, changes, event.Context.ActorID, event.Context.CorrelationID, event.Context.RemoteAddress, event.Context.RemotePort, event.At, event.Context.ActorEmail)
	if errors.Is(err, sql.ErrNoRows) {
		return errors.New("user audit content type missing")
	}
	if err != nil {
		return credentialTransactionError(err)
	}
	if event.AssociationID != nil {
		_, err = t.tx.ExecContext(ctx, `INSERT INTO application_auditlogindex
   (audit_log_index_id,log_entry_id,sport_association_id,resolution_path,created_at)
   VALUES($1,$2,$3,$4,$5)`, uuid.NewString(), id, event.AssociationID, event.ResolutionPath, event.At)
	}
	return credentialTransactionError(err)
}
func (t *loginPreparationTransaction) Commit() error {
	return credentialTransactionError(t.tx.Commit())
}
func (t *loginPreparationTransaction) Rollback() error { return t.tx.Rollback() }
