package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

var _ domain.SubscriptionUpdateStore = (*SubscriptionLifecycles)(nil)

// BeginSubscriptionUpdate opens the transaction used by subscription/<uid>/update.
func (r *SubscriptionLifecycles) BeginSubscriptionUpdate(ctx context.Context) (domain.SubscriptionUpdateTransaction, error) {
	tx, err := r.BeginSubscriptionLifecycle(ctx)
	if err != nil {
		return nil, err
	}
	return &subscriptionUpdateTransaction{subscriptionLifecycleTransaction: tx.(*subscriptionLifecycleTransaction)}, nil
}

type subscriptionUpdateTransaction struct {
	*subscriptionLifecycleTransaction
}

func (t *subscriptionUpdateTransaction) OwnerAssociation(ctx context.Context, owner string, scope *string) (string, error) {
	return paymentCategoryAssociation(ctx, t.tx, owner, scope, false)
}

func (t *subscriptionUpdateTransaction) Subscription(ctx context.Context, id string, lookup domain.SubscriptionUpdateLookup) (*domain.SubscriptionUpdateRow, error) {
	var row domain.SubscriptionUpdateRow
	err := t.tx.GetContext(ctx, &row, `SELECT s.subscription_id, s.sport_association_id, s.type, s.role, s.status_flag, s.start_date,
 s.end_date, s.acceptance_date, s.resignation_date, s.subscription_number, s.subscription_type, s.custom_data::text AS custom_data,
 s.additional_fields::text AS additional_fields, s.notes, s.document_pdf_id, s.creation_date, u.subscription_start_day,
 u.subscription_start_month, u.custom_end_date, u.subscription_end_day, u.subscription_end_month, u.subscription_duration
 FROM application_subscription s JOIN application_sportassociation a ON a.sport_association_id=s.sport_association_id
 JOIN bakney_user u ON u.user_id=a.user_id
 WHERE s.subscription_id=$1 AND ($2::uuid IS NULL OR s.sport_association_id=$2::uuid) AND ($3::uuid IS NULL OR s.group_id=$3::uuid)
 AND ($4::uuid IS NULL OR s.user_id=$4::uuid)
 FOR UPDATE OF s`, id, lookup.Scope, lookup.Group, lookup.User)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// SaveSubscription is Subscription.save() of the updated instance: every
// column update_subscription assigns and document_pdf (cleared); the entry,
// if any, is indexed directly to the subscription's association.
func (t *subscriptionUpdateTransaction) SaveSubscription(ctx context.Context, save domain.SubscriptionUpdateSave, audit domain.CourseSubscriptionAudit) error {
	if err := t.AuditOnly(ctx, save, audit); err != nil {
		return err
	}
	_, err := t.tx.ExecContext(ctx, `UPDATE application_subscription SET type=$2, role=$3, status_flag=$4, start_date=$5::date,
 end_date=$6::date, acceptance_date=$7::date, resignation_date=$8::date, subscription_number=$9, subscription_type=$10, notes=$11,
 custom_data=$12::jsonb, additional_fields=$13::jsonb, document_pdf_id=NULL WHERE subscription_id=$1`, save.ID, save.Type, save.Role,
		save.Status, save.Start, save.End, save.Acceptance, save.Resignation, save.Number, save.Kind, save.Notes, save.Custom, save.Additional)
	return err
}

// AuditOnly is auditlog's pre_save entry (none without changes).
func (t *subscriptionUpdateTransaction) AuditOnly(ctx context.Context, save domain.SubscriptionUpdateSave, audit domain.CourseSubscriptionAudit) error {
	if len(save.Changes) == 0 {
		return nil
	}
	association := save.AssociationID
	return appendModelAudit(ctx, t.tx, courseSubscriptionEntry("subscription", "Subscription", save.ID, "", 1, save.Changes, &association, "direct", audit))
}
