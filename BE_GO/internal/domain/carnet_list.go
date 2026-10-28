package domain

import (
	"context"
	"errors"
	"time"
)

// CarnetListCarnet is one association carnet with the columns
// CarnetListSerializer renders; Fee is the numeric(9,2) text, Meta the jsonb text.
type CarnetListCarnet struct {
	ID            string    `db:"carnet_id"`
	Title         *string   `db:"title"`
	Description   *string   `db:"description"`
	LessonsNumber int       `db:"lessons_number"`
	CreationDate  time.Time `db:"creation_date"`
	Fee           string    `db:"fee"`
	Public        bool      `db:"public"`
	Meta          *string   `db:"meta"`
}

// CarnetListHolding is one carnet subscription with its carnet, its
// subscription's dates and associate (all FK reads use base managers) and its
// live course subscriptions as a JSON array.
type CarnetListHolding struct {
	ID                  string     `db:"carnet_subscription_id"`
	UserID              *string    `db:"user_id"`
	CarnetID            *string    `db:"carnet_id"`
	CarnetTitle         *string    `db:"carnet_title"`
	SubscriptionID      *string    `db:"subscription_id"`
	StartDate           *time.Time `db:"start_date"`
	EndDate             *time.Time `db:"end_date"`
	AssociateID         *string    `db:"associate_id"`
	Address             *string    `db:"address"`
	AddressCap          *string    `db:"address_cap"`
	AddressCity         *string    `db:"address_city"`
	BornCity            *string    `db:"born_city"`
	BornDate            *time.Time `db:"born_date"`
	Email               *string    `db:"email"`
	Sex                 *string    `db:"sex"`
	FirstName           *string    `db:"first_name"`
	LastName            *string    `db:"last_name"`
	IsMinor             *bool      `db:"is_minor"`
	AssociationID       *string    `db:"sport_association_id"`
	TaxCode             *string    `db:"tax_code"`
	AssociateUserID     *string    `db:"associate_user_id"`
	OwnerUserID         *string    `db:"association_user_id"`
	Phone               *string    `db:"phone"`
	FamilyID            *string    `db:"family_id"`
	FamilyRole          *string    `db:"family_role"`
	CreationDate        time.Time  `db:"creation_date"`
	PaymentID           *string    `db:"payment_id"`
	Meta                *string    `db:"meta"`
	CourseSubscriptions string     `db:"course_subscriptions"`
}

// CarnetHoldingScope is scoped_queryset(CarnetSubscription) for an
// association-scoped session: the association, and for an athlete the family
// visibility (optionally within a group).
type CarnetHoldingScope struct {
	AssociationID string
	Family        bool
	GroupID       *string
}

type CarnetListStore interface {
	// OwnerCarnets is SportAssociation.get(user=owner) (ErrAssociationNotFound
	// when absent) and its carnets, unordered.
	OwnerCarnets(ctx context.Context, owner string, scope *string) ([]CarnetListCarnet, error)
	// Holdings lists carnet subscriptions held by user directly or through a
	// subscription's associate, newest first.
	Holdings(ctx context.Context, user string, scope *CarnetHoldingScope) ([]CarnetListHolding, error)
}

// CarnetInfoHolding adds the courses carnet_info lists for the holding's
// subscription (every live course subscription, not the M2M links).
type CarnetInfoHolding struct {
	CarnetListHolding
	SubscriptionCourses string `db:"subscription_courses"`
}

type CarnetInfoStore interface {
	// OwnedCarnet is Carnet.get(carnet_id=id, sport_association__user=owner)
	// within the impersonation association; ErrCarnetNotFound when absent.
	OwnedCarnet(ctx context.Context, owner, id string, scope *string) (CarnetListCarnet, error)
	// CarnetHoldings lists the carnet's subscriptions, newest first.
	CarnetHoldings(ctx context.Context, carnet string, scope *string) ([]CarnetInfoHolding, error)
}

var ErrCarnetNotFound = errors.New("carnet not found")
