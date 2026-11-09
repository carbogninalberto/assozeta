package repository

import (
	"context"
	"database/sql"
	"errors"
	"strconv"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// SubscriptionSigns persists subscription/sign (subscription-sign.md).
type SubscriptionSigns struct{ db *sqlx.DB }

func NewSubscriptionSigns(db *sqlx.DB) *SubscriptionSigns { return &SubscriptionSigns{db: db} }

var _ domain.SubscriptionSignStore = (*SubscriptionSigns)(nil)

const subscriptionSignColumns = `SELECT subscription_id::text, sport_association_id::text, status_flag, signature_storage_key, signature_url
 FROM application_subscription WHERE subscription_id=$1::uuid AND ($2::uuid IS NULL OR sport_association_id=$2::uuid)
 AND ($3::uuid IS NULL OR group_id=$3::uuid)`

func subscriptionSignFind(ctx context.Context, q sqlx.QueryerContext, lookup domain.SubscriptionSignLookup, lock bool) (*domain.SubscriptionSignRow, error) {
	var scope *string
	if lookup.Scope != nil {
		scope = &lookup.Scope.AssociationID
	}
	query := subscriptionSignColumns
	if lock {
		query += " FOR UPDATE"
	}
	var row domain.SubscriptionSignRow
	if err := sqlx.GetContext(ctx, q, &row, query, lookup.ID, scope, lookup.Group); errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	} else if err != nil {
		return nil, err
	}
	// scoped_queryset narrows an athlete's session to the athlete's family rows.
	if lookup.Scope != nil && lookup.Scope.Role == domain.RoleAthlete {
		scope := *lookup.Scope
		if lookup.Group != nil {
			scope.GroupID = *lookup.Group
		}
		visible, err := NewScopedResources(q).Available(ctx, scope, "subscription", []string{row.ID})
		if err != nil {
			return nil, err
		}
		if len(visible) == 0 {
			return nil, nil
		}
	}
	return &row, nil
}

func (r *SubscriptionSigns) Subscription(ctx context.Context, lookup domain.SubscriptionSignLookup) (*domain.SubscriptionSignRow, error) {
	return subscriptionSignFind(ctx, r.db, lookup, false)
}

func (r *SubscriptionSigns) KeyReferenced(ctx context.Context, key string) (bool, error) {
	var exists bool
	err := r.db.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_subscription WHERE signature_storage_key=$1)`, key)
	return exists, err
}

type subscriptionSignTransaction struct{ *ownedTransaction }

func (r *SubscriptionSigns) BeginSubscriptionSign(ctx context.Context) (domain.SubscriptionSignTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &subscriptionSignTransaction{ownedTransaction: tx}, nil
}

func (t *subscriptionSignTransaction) Lock(ctx context.Context, lookup domain.SubscriptionSignLookup) (*domain.SubscriptionSignRow, error) {
	return subscriptionSignFind(ctx, t.tx, lookup, true)
}

func subscriptionSignText(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

func (t *subscriptionSignTransaction) Save(ctx context.Context, row domain.SubscriptionSignRow, save domain.SubscriptionSignSave) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_signature (signature_id, user_id, signature) VALUES ($1, $2, $3)`,
		save.SignatureID, save.User, save.Data); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_signature", "signature_id", save.SignatureID)
	if err != nil {
		return err
	}
	// Signature has no subscription to resolve: the actor's own association, if any.
	var association *string
	if save.Audit.ActorID != nil {
		var id string
		err := t.tx.GetContext(ctx, &id, `SELECT sport_association_id::text FROM application_sportassociation
 WHERE user_id=$1 AND NOT deleted ORDER BY sport_association_id LIMIT 1`, *save.Audit.ActorID)
		if err == nil {
			association = &id
		} else if !errors.Is(err, sql.ErrNoRows) {
			return err
		}
	}
	var path *string
	if association != nil {
		value := "actor.sport_association"
		path = &value
	}
	if err := appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "signature", Key: save.SignatureID,
		Representation: "Signature object (" + save.SignatureID + ")", Action: 0, Changes: changes, Context: save.Audit,
		AssociationID: association, ResolutionPath: path, At: save.At}); err != nil {
		return err
	}
	// save(update_fields=['signature_url', 'signature_storage_key', 'status_flag']).
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_subscription SET signature_url=$2, signature_storage_key=$3, status_flag=2
 WHERE subscription_id=$1::uuid`, row.ID, save.URL, save.Key); err != nil {
		return err
	}
	updated := map[string][2]string{}
	if subscriptionSignText(row.URL) != subscriptionSignText(save.URL) {
		updated["signature_url"] = [2]string{subscriptionSignText(row.URL), subscriptionSignText(save.URL)}
	}
	if subscriptionSignText(row.Key) != subscriptionSignText(save.Key) {
		updated["signature_storage_key"] = [2]string{subscriptionSignText(row.Key), subscriptionSignText(save.Key)}
	}
	if row.Status != 2 {
		updated["status_flag"] = [2]string{strconv.Itoa(row.Status), "2"}
	}
	if len(updated) == 0 {
		return nil
	}
	direct := "direct"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "subscription", Key: row.ID,
		Representation: "Subscription object (" + row.ID + ")", Action: 1, Changes: updated, Context: save.Audit,
		AssociationID: &row.Association, ResolutionPath: &direct, At: save.At})
}
