package domain

import "context"

// PersonaSubsWriteStore opens the transaction of the DefaultRouter
// personas-subscriptions create and update (AssociateSubscriptionSerializer).
type PersonaSubsWriteStore interface {
	BeginPersonaSubsWrite(ctx context.Context) (PersonaSubsWriteTransaction, error)
}

type PersonaSubsWriteTransaction interface {
	// Related is queryset.get(pk=id) on the related field's default manager
	// and the owner tenant_scope resolves (nil: no association).
	Related(ctx context.Context, field, id string, group *string) (found bool, owner *string, err error)
	// TenantAssociation is SportAssociation.objects.filter(user=user).first().
	TenantAssociation(ctx context.Context, user string) (*string, error)
	// Subscription reads the row by key (locked), any state.
	Subscription(ctx context.Context, id string) (*PersonaSubscriptionRow, error)
	// DateRule is the association owner's subscription settings.
	DateRule(ctx context.Context, association string) (SubscriptionDateRule, error)
	// TagIDs are the subscription's tag links.
	TagIDs(ctx context.Context, subscription string) ([]string, error)
	// SaveSubscription is Model.save(): the update entry (when changes is not
	// empty), then every column.
	SaveSubscription(ctx context.Context, row PersonaSubscriptionRow, changes map[string][2]string, audit CourseSubscriptionAudit) error
	// AuditSubscription is the pre_save update entry alone: the save that
	// follows fails.
	AuditSubscription(ctx context.Context, id string, changes map[string][2]string, audit CourseSubscriptionAudit) error
	RemoveTags(ctx context.Context, subscription string, tags []string) error
	AddTag(ctx context.Context, subscription, tag string) error
	Commit() error
	Rollback() error
}
