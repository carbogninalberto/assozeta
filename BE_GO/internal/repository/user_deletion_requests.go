package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

type UserDeletionRequests struct{ db *sqlx.DB }

func NewUserDeletionRequests(db *sqlx.DB) *UserDeletionRequests {
	return &UserDeletionRequests{db: db}
}

func (r *UserDeletionRequests) BeginUserDeletionRequests(ctx context.Context, job domain.Job) (domain.UserDeletionRequestTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	if err = fenceJobEffects(ctx, tx.tx, job.ID, job.LeaseToken); err != nil {
		_ = tx.Rollback()
		return nil, err
	}
	return &userDeletionRequestTransaction{ownedTransaction: tx}, nil
}

type userDeletionRequestTransaction struct{ *ownedTransaction }

// User.objects is the deleted=False manager; the task adds is_active and the
// delete_on date filters. Like the task's queryset, no ordering is applied.
func (t *userDeletionRequestTransaction) DueUsers(ctx context.Context, today time.Time) ([]domain.UserDeletionCandidate, error) {
	var rows []struct {
		ID        uuid.UUID `db:"user_id"`
		Email     string    `db:"email"`
		Username  string    `db:"username"`
		FirstName string    `db:"first_name"`
		LastName  string    `db:"last_name"`
	}
	err := t.tx.SelectContext(ctx, &rows, `SELECT user_id,email,username,first_name,last_name FROM bakney_user
 WHERE NOT deleted AND is_active AND delete_on IS NOT NULL AND delete_on<=$1::date`, today.Format(time.DateOnly))
	if err != nil {
		return nil, err
	}
	users := make([]domain.UserDeletionCandidate, len(rows))
	for i, row := range rows {
		users[i] = domain.UserDeletionCandidate{ID: row.ID, Email: row.Email, Username: row.Username, FirstName: row.FirstName, LastName: row.LastName}
	}
	return users, nil
}

// MarkDeleted mirrors save(): auditlog's pre_save diff reads the current row
// through User.objects (deleted=False) and records only changed fields, then
// the index resolver maps the still-visible user to its first non-deleted
// association by primary key. The UPDATE follows the audit, as in Django.
func (t *userDeletionRequestTransaction) MarkDeleted(ctx context.Context, user domain.UserDeletionCandidate, at time.Time) error {
	var current struct {
		Email    string  `db:"email"`
		Username string  `db:"username"`
		Active   bool    `db:"is_active"`
		Deleted  bool    `db:"deleted"`
		DeleteOn *string `db:"delete_on"`
	}
	err := t.tx.GetContext(ctx, &current, `SELECT email,username,is_active,deleted,delete_on::text AS delete_on
 FROM bakney_user WHERE user_id=$1 AND NOT deleted FOR UPDATE`, user.ID)
	if errors.Is(err, sql.ErrNoRows) {
		return errors.New("user deletion request row changed")
	}
	if err != nil {
		return err
	}
	email, username := user.Email+"__deleted", user.Username+"__deleted"
	changes := map[string][2]string{}
	add := func(name, old, new string) {
		if old != new {
			changes[name] = [2]string{old, new}
		}
	}
	add("email", current.Email, email)
	add("username", current.Username, username)
	add("is_active", pythonBool(current.Active), "False")
	add("deleted", pythonBool(current.Deleted), "True")
	add("delete_on", optionalText(current.DeleteOn), "None")
	if len(changes) != 0 {
		var association *string
		var found string
		err = t.tx.GetContext(ctx, &found, `SELECT sport_association_id::text FROM application_sportassociation
 WHERE user_id=$1 AND NOT deleted ORDER BY sport_association_id LIMIT 1`, user.ID)
		if err == nil {
			association = &found
		} else if !errors.Is(err, sql.ErrNoRows) {
			return err
		}
		path := "user.sport_association"
		// User.__str__ of the in-memory instance.
		representation := "name: " + user.FirstName + " " + user.LastName
		if err = appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "user", Key: user.ID.String(), Representation: representation,
			Action: 1, Changes: changes, AssociationID: association, ResolutionPath: &path, At: at}); err != nil {
			return err
		}
	}
	// auditlog writes in pre_save and autocommits before the UPDATE: a failed
	// UPDATE (for example a taken username) keeps the audit entry.
	if _, err = t.tx.ExecContext(ctx, `SAVEPOINT user_deletion_save`); err != nil {
		return err
	}
	_, err = t.tx.ExecContext(ctx, `UPDATE bakney_user SET email=$2,username=$3,is_active=false,deleted=true,delete_on=NULL
 WHERE user_id=$1`, user.ID, email, username)
	if err == nil || ctx.Err() != nil {
		return err
	}
	if _, rollbackErr := t.tx.ExecContext(ctx, `ROLLBACK TO SAVEPOINT user_deletion_save`); rollbackErr != nil {
		return rollbackErr
	}
	return domain.ErrUserDeletionSaveFailed
}

func (t *userDeletionRequestTransaction) CompleteJob(ctx context.Context, id, token uuid.UUID) error {
	return completeJob(ctx, t.tx, id, token, nil)
}
func (t *userDeletionRequestTransaction) Commit() error   { return t.ownedTransaction.Commit() }
func (t *userDeletionRequestTransaction) Rollback() error { return t.ownedTransaction.Rollback() }
