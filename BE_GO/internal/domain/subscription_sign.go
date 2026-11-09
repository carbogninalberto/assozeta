package domain

import (
	"context"
	"time"
)

// SubscriptionSignRow is the subscription subscription/sign resolves.
type SubscriptionSignRow struct {
	ID          string  `db:"subscription_id"`
	Association string  `db:"sport_association_id"`
	Status      int     `db:"status_flag"`
	Key         *string `db:"signature_storage_key"`
	URL         *string `db:"signature_url"`
}

// SubscriptionSignLookup is scoped_queryset(request, Subscription.objects)
// for one id: the group-aware manager (Group), the session association
// (Scope) and, for an athlete in a scoped session, the athlete's family rows.
type SubscriptionSignLookup struct {
	ID    string
	Group *string
	Scope *ResourceScope
}

// SubscriptionSignSave is the signature saved on the locked row.
type SubscriptionSignSave struct {
	SignatureID, User, Data string
	Key, URL                *string
	Audit                   LoginAuditContext
	At                      time.Time
}

type SubscriptionSignStore interface {
	Subscription(ctx context.Context, lookup SubscriptionSignLookup) (*SubscriptionSignRow, error)
	// KeyReferenced is Subscription._base_manager.filter(signature_storage_key=key).exists().
	KeyReferenced(ctx context.Context, key string) (bool, error)
	BeginSubscriptionSign(ctx context.Context) (SubscriptionSignTransaction, error)
}

type SubscriptionSignTransaction interface {
	// Lock is the same lookup with select_for_update (nil: DoesNotExist).
	Lock(ctx context.Context, lookup SubscriptionSignLookup) (*SubscriptionSignRow, error)
	// Save creates the Signature row (create entry indexed through the actor's own
	// association) and saves the subscription's signature columns and status
	// PENDING (update entry with the changed fields, indexed direct).
	Save(ctx context.Context, row SubscriptionSignRow, save SubscriptionSignSave) error
	Commit() error
	Rollback() error
}
