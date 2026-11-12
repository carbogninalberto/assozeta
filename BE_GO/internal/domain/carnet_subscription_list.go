package domain

import (
	"context"
	"errors"
	"time"
)

// ErrCarnetSubscriptionListSubscription is {'msg': 'Subscription not found.'}.
var ErrCarnetSubscriptionListSubscription = errors.New("carnet subscription list subscription not found")

type CarnetListedSubscription struct {
	ID            string  `db:"subscription_id"`
	AssociateID   *string `db:"associate_id"`
	AssociationID *string `db:"sport_association_id"`
}

type CarnetListedHolding struct {
	ID                  string     `db:"carnet_subscription_id"`
	CarnetID            *string    `db:"carnet_id"`
	Title               *string    `db:"title"`
	Fee                 *string    `db:"fee"`
	CreationDate        time.Time  `db:"creation_date"`
	SubscriptionID      string     `db:"subscription_id"`
	Disabled            bool       `db:"disabled"`
	PaymentID           *string    `db:"payment_id"`
	PaymentAmount       *string    `db:"payment_amount"`
	PaymentCreationDate *time.Time `db:"payment_creation_date"`
	Meta                []byte     `db:"meta"`
}

type CarnetListedCourse struct {
	HoldingID            string  `db:"carnetsubscription_id"`
	CourseID             string  `db:"course_id"`
	CourseTitle          *string `db:"course_title"`
	CourseSubscriptionID string  `db:"course_subscription_id"`
}

type CarnetSubscriptionListStore interface {
	// AthleteSubscriptionIDs is Subscription.objects (group-aware, scoped)
	// .filter(user=user) as canonical text.
	AthleteSubscriptionIDs(ctx context.Context, user string, scope *CarnetHoldingScope, group *string) ([]string, error)
	// ListedSubscription is the group-aware, scoped subscription by key, of
	// the owner's associations when owner is set.
	ListedSubscription(ctx context.Context, id string, owner *string, scope *CarnetHoldingScope, group *string, user string) (*CarnetListedSubscription, error)
	// ListedHoldings are the carnet subscriptions of the subscription or of
	// its associate's subscriptions in the same association, with their live
	// course links.
	ListedHoldings(ctx context.Context, subscription CarnetListedSubscription, scope *CarnetHoldingScope, group *string, user string) ([]CarnetListedHolding, []CarnetListedCourse, error)
}
