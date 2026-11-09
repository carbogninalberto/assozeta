package domain

import (
	"context"
	"time"
)

// SubscriptionTransferRow is the subscription subscription_transfer reads.
type SubscriptionTransferRow struct {
	ID            string  `db:"subscription_id"`
	AssociationID string  `db:"sport_association_id"`
	UserID        string  `db:"user_id"`
	AssociateID   *string `db:"associate_id"`
	DatesSet      bool    `db:"dates_set"`
	Denomination  *string `db:"denomination"`
}

// SubscriptionTransferPerson is an associate (the subscription's or its tutor).
type SubscriptionTransferPerson struct {
	ID            string  `db:"associate_id"`
	AssociationID string  `db:"sport_association_id"`
	FirstName     *string `db:"first_name"`
	LastName      *string `db:"last_name"`
	UserID        *string `db:"user_id"`
}

// SubscriptionTransferTutor is get_main_tutor's relation: the first primary
// relation, else the first relation (Primary false: it becomes primary).
type SubscriptionTransferTutor struct {
	RelationID int64                      `db:"id"`
	Primary    bool                       `db:"is_primary"`
	Tutor      SubscriptionTransferPerson `db:"tutor"`
}

// SubscriptionTransferAssociation is request.user.sport_association.
type SubscriptionTransferAssociation struct {
	ID           string  `db:"sport_association_id"`
	Denomination *string `db:"denomination"`
}

// SubscriptionTransferCreated is the transfer row the serializer saved.
type SubscriptionTransferCreated struct {
	ID, SubscriptionID, AssociationID, Requester, Recipient, Token string
	Created, Expires                                               time.Time
}

// SubscriptionTransferStore persists subscription/<uid>/transfer. Every
// write is its own autocommitted statement or save(), as in the view.
type SubscriptionTransferStore interface {
	Subscription(ctx context.Context, id string, scope, group *string) (*SubscriptionTransferRow, error)
	// Pending reports a PENDING transfer of the subscription; with notExpiredAt,
	// only one the expiry update at that time would leave pending (read-only).
	Pending(ctx context.Context, subscription string, notExpiredAt *time.Time) (bool, error)
	ExpireTransfers(ctx context.Context, at time.Time) error
	Recipient(ctx context.Context, id, requester string) (*string, error)
	Associate(ctx context.Context, id string) (*SubscriptionTransferPerson, error)
	MainTutor(ctx context.Context, associate string) (*SubscriptionTransferTutor, error)
	MakePrimary(ctx context.Context, tutor SubscriptionTransferTutor, association string, audit LoginAuditContext, at time.Time) error
	OwnerAssociation(ctx context.Context, user string) (*SubscriptionTransferAssociation, error)
	InstanceConfig(ctx context.Context) (EmailRenderInstanceConfig, error)
	CreateUser(ctx context.Context, user OAuthNewUser, audit LoginAuditContext) error
	CreateTransfer(ctx context.Context, transfer SubscriptionTransferCreated, audit LoginAuditContext) error
	SaveSubscriptionUser(ctx context.Context, row SubscriptionTransferRow, user string, audit LoginAuditContext, at time.Time) error
	SaveAssociateUser(ctx context.Context, person SubscriptionTransferPerson, user string, audit LoginAuditContext, at time.Time) error
	AcceptTransfer(ctx context.Context, transfer SubscriptionTransferCreated, at time.Time, audit LoginAuditContext) error
}
