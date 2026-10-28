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

// CarnetEditable is a locked carnet's stored update fields.
type CarnetEditable struct {
	AssociationID string  `db:"sport_association_id"`
	Title         *string `db:"title"`
	Description   *string `db:"description"`
	Lessons       int64   `db:"lessons_number"`
	Fee           string  `db:"fee"`
	Public        bool    `db:"public"`
}

type CarnetUpdateWrite struct {
	ID, Title, Description, Fee string
	Lessons                     int64
	Public                      bool
	Changes                     map[string][2]string
	Context                     LoginAuditContext
	At                          time.Time
}

type CarnetUpdateTransaction interface {
	CarnetForEdit(ctx context.Context, id, owner string, scope *string) (CarnetEditable, error)
	UpdateCarnet(ctx context.Context, write CarnetUpdateWrite, association string) error
	Commit() error
	Rollback() error
}

type CarnetUpdateStore interface {
	BeginCarnetUpdate(context.Context) (CarnetUpdateTransaction, error)
}
