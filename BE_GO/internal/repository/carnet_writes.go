package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type CarnetWrites struct{ db *sqlx.DB }

func NewCarnetWrites(db *sqlx.DB) *CarnetWrites { return &CarnetWrites{db: db} }

var _ domain.CarnetDeleteStore = (*CarnetWrites)(nil)

func (r *CarnetWrites) BeginCarnetWrite(ctx context.Context) (domain.CarnetWriteTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &carnetWriteTransaction{dashboardLayoutTransaction: &dashboardLayoutTransaction{
		paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}}, nil
}

type carnetWriteTransaction struct{ *dashboardLayoutTransaction }

func (t *carnetWriteTransaction) OwnedCarnetForUpdate(ctx context.Context, id, owner string, scope *string) (string, error) {
	var association string
	err := t.tx.GetContext(ctx, &association, `SELECT c.sport_association_id FROM application_carnet c
 JOIN application_sportassociation sa ON sa.sport_association_id=c.sport_association_id
 WHERE c.carnet_id=$1 AND sa.user_id=$2 AND ($3::uuid IS NULL OR c.sport_association_id=$3::uuid) FOR UPDATE OF c`, id, owner, scope)
	if errors.Is(err, sql.ErrNoRows) {
		return "", domain.ErrCarnetNotFound
	}
	return association, err
}

// DeleteCarnet mirrors carnet.delete(): the collector removes the course links
// (no audit), each carnet subscription and then the carnet, logging every
// deleted model row. Deleted rows no longer resolve, so subscriptions (no
// association field) fall back to the actor's association and the carnet to
// its changes.
func (t *carnetWriteTransaction) DeleteCarnet(ctx context.Context, id, association string, actorAssociation *string, audit domain.LoginAuditContext, at time.Time) error {
	holdings := []string{}
	if err := t.tx.SelectContext(ctx, &holdings, `SELECT carnet_subscription_id FROM application_carnetsubscription
 WHERE carnet_id_id=$1 ORDER BY carnet_subscription_id FOR UPDATE`, id); err != nil {
		return err
	}
	if _, err := t.tx.ExecContext(ctx, `DELETE FROM application_carnetsubscription_course_subscription
 WHERE carnetsubscription_id=ANY($1::uuid[])`, holdings); err != nil {
		return err
	}
	actorPath := "actor.sport_association"
	for _, holding := range holdings {
		changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_carnetsubscription", "carnet_subscription_id", holding)
		if err != nil {
			return err
		}
		entry := modelAuditEntry{Model: "carnetsubscription", Key: holding, Representation: "CarnetSubscription object (" + holding + ")",
			Action: 2, Changes: changes, Context: audit, At: at}
		if actorAssociation != nil {
			entry.AssociationID, entry.ResolutionPath = actorAssociation, &actorPath
		}
		if err = appendModelAudit(ctx, t.tx, entry); err != nil {
			return err
		}
	}
	changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_carnet", "carnet_id", id)
	if err != nil {
		return err
	}
	path := "changes_json.sport_association"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "carnet", Key: id, Representation: "Carnet object (" + id + ")",
		Action: 2, Changes: changes, Context: audit, AssociationID: &association, ResolutionPath: &path, At: at})
}

var _ domain.CarnetUpdateStore = (*CarnetWrites)(nil)

func (r *CarnetWrites) BeginCarnetUpdate(ctx context.Context) (domain.CarnetUpdateTransaction, error) {
	tx, err := r.BeginCarnetWrite(ctx)
	if err != nil {
		return nil, err
	}
	return tx.(*carnetWriteTransaction), nil
}

func (t *carnetWriteTransaction) CarnetForEdit(ctx context.Context, id, owner string, scope *string) (domain.CarnetEditable, error) {
	var row domain.CarnetEditable
	err := t.tx.GetContext(ctx, &row, `SELECT c.sport_association_id, c.title, c.description, c.lessons_number, c.fee::text AS fee, c.public
 FROM application_carnet c JOIN application_sportassociation sa ON sa.sport_association_id=c.sport_association_id
 WHERE c.carnet_id=$1 AND sa.user_id=$2 AND ($3::uuid IS NULL OR c.sport_association_id=$3::uuid) FOR UPDATE OF c`, id, owner, scope)
	if errors.Is(err, sql.ErrNoRows) {
		return row, domain.ErrCarnetNotFound
	}
	return row, err
}

// UpdateCarnet writes the serializer's fields and, when auditlog would log
// changes, one update entry indexed directly to the carnet's association.
func (t *carnetWriteTransaction) UpdateCarnet(ctx context.Context, write domain.CarnetUpdateWrite, association string) error {
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_carnet SET title=$2, description=$3, fee=$4::numeric, lessons_number=$5, public=$6
 WHERE carnet_id=$1`, write.ID, write.Title, write.Description, write.Fee, write.Lessons, write.Public); err != nil {
		return err
	}
	if len(write.Changes) == 0 {
		return nil
	}
	path := "direct"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "carnet", Key: write.ID, Representation: "Carnet object (" + write.ID + ")",
		Action: 1, Changes: write.Changes, Context: write.Context, AssociationID: &association, ResolutionPath: &path, At: write.At})
}
