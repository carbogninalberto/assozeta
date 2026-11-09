package domain

import "context"

// SubscriptionSignature is a subscription's signature columns.
type SubscriptionSignature struct {
	Key *string `db:"signature_storage_key"`
	URL *string `db:"signature_url"`
}

// Present is Subscription.has_signature: a non-empty key or URL.
func (s SubscriptionSignature) Present() bool {
	return (s.Key != nil && *s.Key != "") || (s.URL != nil && *s.URL != "")
}

type SubscriptionApproveStore interface {
	BeginSubscriptionApprove(context.Context) (SubscriptionApproveTransaction, error)
}

// SubscriptionApproveTransaction extends the lifecycle transaction with
// subscription_approve's signature reuse.
type SubscriptionApproveTransaction interface {
	SubscriptionLifecycleTransaction
	Signature(ctx context.Context, id string) (SubscriptionSignature, error)
	// PastSignature is the first row of scoped_queryset(Subscription.objects)
	// .filter(associate=..., status_flag__in=[ACCEPTED, PENDING]) with a
	// signature column not null, excluding the row, by -creation_date; nil when none.
	PastSignature(ctx context.Context, id string, associate, scope, group *string) (*SubscriptionSignature, error)
	SaveSignature(ctx context.Context, id string, signature SubscriptionSignature) error
}
