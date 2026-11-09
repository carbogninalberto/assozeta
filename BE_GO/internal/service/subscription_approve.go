package service

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

// ErrSubscriptionApproveAccepted is PermissionDenied('User cannot change the
// status of the subscription.') on an already accepted row.
var ErrSubscriptionApproveAccepted = errors.New("subscription already accepted")

// subscriptionApprovePublishTimeout bounds the publish after the commit; it
// does not follow the request's cancellation (Django publishes regardless).
const subscriptionApprovePublishTimeout = 10 * time.Second

type SubscriptionApprovals struct {
	store     domain.SubscriptionApproveStore
	publisher celery.Publisher
	now       func() time.Time
}

func NewSubscriptionApprovals(store domain.SubscriptionApproveStore, publisher celery.Publisher, now func() time.Time) *SubscriptionApprovals {
	return &SubscriptionApprovals{store: store, publisher: publisher, now: now}
}

// approveStore lets the lifecycle prologue open the approve transaction.
type approveStore struct {
	store domain.SubscriptionApproveStore
	tx    *domain.SubscriptionApproveTransaction
}

func (a approveStore) BeginSubscriptionLifecycle(ctx context.Context) (domain.SubscriptionLifecycleTransaction, error) {
	tx, err := a.store.BeginSubscriptionApprove(ctx)
	if err != nil {
		return nil, err
	}
	*a.tx = tx
	return tx, nil
}

// pythonDateTime is str() of an aware UTC datetime (auditlog's rendering of
// the DateField's in-memory timezone.now()).
func pythonDateTime(value time.Time) string {
	text := value.UTC().Format("2006-01-02 15:04:05")
	if micro := value.Nanosecond() / 1000; micro != 0 {
		text += fmt.Sprintf(".%06d", micro)
	}
	return text + "+00:00"
}

func optionalText(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

// Approve follows subscription_approve: the shared prologue, the accepted
// check, the signature reuse, Subscription.save() (committed), then the
// subscription_approved signal's check_workflows_trigger.delay(...).
func (s *SubscriptionApprovals) Approve(ctx context.Context, identity domain.ResolvedIdentity, raw, groupHeader string, login domain.LoginAuditContext) error {
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return err
	}
	var tx domain.SubscriptionApproveTransaction
	lifecycles := &SubscriptionLifecycles{store: approveStore{store: s.store, tx: &tx}, now: s.now}
	request, err := lifecycles.open(ctx, identity, id, groupHeader, login, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	row := request.row
	if row.Status == 4 {
		return ErrSubscriptionApproveAccepted
	}
	now := request.audit.At
	changes := map[string][2]string{}
	signature, err := tx.Signature(ctx, row.ID)
	if err != nil {
		return err
	}
	var copied *domain.SubscriptionSignature
	if !signature.Present() {
		past, err := tx.PastSignature(ctx, row.ID, row.AssociateID, request.scope, request.group)
		if err != nil {
			return err
		}
		if past != nil && past.Present() {
			copied = past
			for name, pair := range map[string][2]*string{"signature_storage_key": {signature.Key, past.Key}, "signature_url": {signature.URL, past.URL}} {
				if optionalText(pair[0]) != optionalText(pair[1]) {
					changes[name] = [2]string{optionalText(pair[0]), optionalText(pair[1])}
				}
			}
		}
	}
	save, err := subscriptionSave(ctx, row, 4, civilDate(now), row.Archived, row.PaymentID)
	if err != nil {
		return err
	}
	// The in-memory value is the datetime, never equal to the stored date.
	save.Changes["acceptance_date"] = [2]string{optionalText(lifecycleDateText(lifecycleDate(row.Acceptance))), pythonDateTime(now)}
	for name, pair := range changes {
		save.Changes[name] = pair
	}
	if copied != nil {
		if err = tx.SaveSignature(ctx, row.ID, *copied); err != nil {
			return err
		}
	}
	if err = tx.SaveSubscription(ctx, save, request.audit); err != nil {
		return err
	}
	if err = tx.Commit(); err != nil {
		return err
	}
	// subscription_approved -> subscription_approved_callback publishes right
	// after the autocommitted save; a publish failure is a 500 with the save kept.
	publish, cancel := context.WithTimeout(context.WithoutCancel(ctx), subscriptionApprovePublishTimeout)
	defer cancel()
	_, err = s.publisher.Publish(publish, celery.Task{Name: "check_workflows_trigger", Kwargs: celery.Dict{
		{Key: "trigger_type", Value: "subscription_approved"},
		{Key: "sport_association_id", Value: uuid.MustParse(row.AssociationID)},
		{Key: "subscription_id", Value: uuid.MustParse(row.ID)},
	}})
	return err
}
