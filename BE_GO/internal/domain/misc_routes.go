package domain

import (
	"context"
	"errors"
	"time"
)

// ErrMiscRoutesServer is an exception the Django view raises (the 500 page).
var ErrMiscRoutesServer = errors.New("misc route view raised")

// ErrMiscRoutesNotPorted refuses an input whose Django answer Go does not
// reproduce (the 500 page, before any write).
var ErrMiscRoutesNotPorted = errors.New("misc route input not ported")

// MiscRoutesAnswer is a JSON answer the view returns itself.
type MiscRoutesAnswer struct {
	Status int
	Body   any
}

func (a *MiscRoutesAnswer) Error() string { return "misc route answer" }

// SubscriptionCardToken is a stored SubscriptionToken.
type SubscriptionCardToken struct {
	ID         string    `db:"subscription_token_id"`
	Expiration time.Time `db:"expiration_date"`
}

// SubscriptionCardMember is the scoped subscription with the related rows the
// card renders.
type SubscriptionCardMember struct {
	Number        *string    `db:"subscription_number"`
	Type          *string    `db:"subscription_type"`
	EndDate       *time.Time `db:"end_date"`
	HasAssociate  bool       `db:"has_associate"`
	FirstName     *string    `db:"first_name"`
	LastName      *string    `db:"last_name"`
	AssociationID string     `db:"sport_association_id"`
	Denomination  *string    `db:"denomination"`
	AvatarImage   *string    `db:"avatar_image"`
}

// SubscriptionCardConfiguration is the association's first membership card
// configuration.
type SubscriptionCardConfiguration struct {
	EmitOnlyOnApproval bool    `db:"emit_only_on_approval"`
	Template           *string `db:"customized_template"`
}

type MiscRoutesStore interface {
	BeginMiscRoutes(ctx context.Context) (MiscRoutesTransaction, error)
}

type MiscRoutesTransaction interface {
	Commit() error
	Rollback() error
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	ActorAssociation(ctx context.Context, actor string) (*string, error)
	CreatePost(ctx context.Context, messageID, deliveryID, association, message string, audit LoginAuditContext, at time.Time) error
	CardToken(ctx context.Context, token, subscription string) (*SubscriptionCardToken, error)
	PairingLookup(ctx context.Context) (bool, error)
	DeleteCardToken(ctx context.Context, id string, actorAssociation *string, audit LoginAuditContext, at time.Time) error
	CardMember(ctx context.Context, subscription string, group, scope *string) (*SubscriptionCardMember, error)
	CardConfiguration(ctx context.Context, association string) (*SubscriptionCardConfiguration, error)
	CreateCardConfiguration(ctx context.Context, id, association string, audit LoginAuditContext, at time.Time) error
	ScopedSubscription(ctx context.Context, subscription, association string, group, scope *string) (*string, error)
	CreateCardToken(ctx context.Context, id, subscription, association, token string, created, expires time.Time, audit LoginAuditContext,
		at time.Time) error
}
