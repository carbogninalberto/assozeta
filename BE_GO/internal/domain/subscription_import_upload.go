package domain

import "context"

type SubscriptionImportStore interface {
	BeginSubscriptionImport(context.Context) (SubscriptionImportTransaction, error)
}

type SubscriptionImportTransaction interface {
	// Association is scoped_queryset(request, SportAssociation.objects).filter(user=...).first().
	Association(ctx context.Context, user string, impersonation *string) (*string, error)
	// DocumentExists is Document.objects.filter(document_id=...).exists().
	DocumentExists(ctx context.Context, id string) (bool, error)
	Rollback() error
}
