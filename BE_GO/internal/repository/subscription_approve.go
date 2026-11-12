package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

var _ domain.SubscriptionApproveStore = (*SubscriptionLifecycles)(nil)

// BeginSubscriptionApprove opens the lifecycle transaction used by subscription/<uid>/approve.
func (r *SubscriptionLifecycles) BeginSubscriptionApprove(ctx context.Context) (domain.SubscriptionApproveTransaction, error) {
	tx, err := r.BeginSubscriptionLifecycle(ctx)
	if err != nil {
		return nil, err
	}
	return tx.(*subscriptionLifecycleTransaction), nil
}

func (t *subscriptionLifecycleTransaction) Signature(ctx context.Context, id string) (domain.SubscriptionSignature, error) {
	var signature domain.SubscriptionSignature
	err := t.tx.GetContext(ctx, &signature, `SELECT signature_storage_key, signature_url FROM application_subscription WHERE subscription_id=$1`, id)
	return signature, err
}

// PastSignature: associate=None matches rows without an associate (IS NULL).
func (t *subscriptionLifecycleTransaction) PastSignature(ctx context.Context, id string, associate, scope, group *string) (*domain.SubscriptionSignature, error) {
	var signature domain.SubscriptionSignature
	err := t.tx.GetContext(ctx, &signature, `SELECT signature_storage_key, signature_url FROM application_subscription
 WHERE associate_id IS NOT DISTINCT FROM $1::uuid AND status_flag IN (4, 2)
 AND (signature_storage_key IS NOT NULL OR signature_url IS NOT NULL) AND NOT subscription_id=$2::uuid
 AND ($3::uuid IS NULL OR sport_association_id=$3::uuid) AND ($4::uuid IS NULL OR group_id=$4::uuid)
 ORDER BY creation_date DESC LIMIT 1`, associate, id, scope, group)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &signature, nil
}

func (t *subscriptionLifecycleTransaction) SaveSignature(ctx context.Context, id string, signature domain.SubscriptionSignature) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_subscription SET signature_storage_key=$2, signature_url=$3 WHERE subscription_id=$1`,
		id, signature.Key, signature.URL)
	return err
}
