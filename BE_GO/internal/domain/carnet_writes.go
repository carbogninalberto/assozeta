package domain

import (
	"context"
	"time"
)

type CarnetDeleteStore interface {
	BeginCarnetWrite(context.Context) (CarnetWriteTransaction, error)
}

type CarnetWriteTransaction interface {
	// OwnedCarnetForUpdate locks Carnet.get(carnet_id=id, sport_association__user=owner)
	// within the impersonation association; ErrCarnetNotFound when absent.
	OwnedCarnetForUpdate(ctx context.Context, id, owner string, scope *string) (association string, err error)
	// DeleteCarnet runs the collector cascade with one audit entry per deleted
	// carnet subscription and the carnet; actorAssociation indexes the
	// subscriptions (nil: no index).
	DeleteCarnet(ctx context.Context, id, association string, actorAssociation *string, audit LoginAuditContext, at time.Time) error
	ActiveAssociationOf(ctx context.Context, user string) (*string, error)
	Commit() error
	Rollback() error
}
