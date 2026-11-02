package domain

import (
	"context"
	"errors"
	"time"
)

var (
	// ErrSubscriptionTagNotFound is TagService's ValidationError('tag not found').
	ErrSubscriptionTagNotFound = errors.New("subscription tag not found")
	// ErrSubscriptionTagSubscriptionNotFound is ValidationError('subscription not found').
	ErrSubscriptionTagSubscriptionNotFound = errors.New("subscription tag subscription not found")
)

// SubscriptionTagTarget is the subscription assign/unassign saves: its
// creation date, stored dates and the owner's season settings that
// Subscription.save() reads.
type SubscriptionTagTarget struct {
	Created time.Time
	Dates   SubscriptionDates
	Rule    SubscriptionDateRule
}

type SubscriptionTagTransaction interface {
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	// OrderedTags is Tags.objects.filter(sport_association=...).order_by('tag_name').
	OrderedTags(ctx context.Context, association string) ([]SearchProfileTag, error)
	// TagOf is Tags.objects.filter(tag_id=id, sport_association=association).first().
	TagOf(ctx context.Context, id, association string) (name *string, found bool, err error)
	// SubscriptionOf is the group-aware Subscription.objects.filter(subscription_id=id,
	// sport_association=association).first() (deleted subscriptions included).
	SubscriptionOf(ctx context.Context, id, association string, group *string) (*SubscriptionTagTarget, error)
	// SetLink adds (linked) or removes the M2M row; changed reports whether it did.
	SetLink(ctx context.Context, tag, subscription string, linked bool) (changed bool, err error)
	// SaveSubscriptionDates stores the dates Subscription.save() filled and records their audit update.
	SaveSubscriptionDates(ctx context.Context, id, association string, old, dates SubscriptionDates, audit LoginAuditContext, at time.Time) error
	CreateTag(ctx context.Context, id, name, association string, audit LoginAuditContext, at time.Time) error
	RenameTag(ctx context.Context, id, name, association string, changes map[string][2]string, audit LoginAuditContext, at time.Time) error
	DeleteTag(ctx context.Context, id string, audit LoginAuditContext, at time.Time) error
	Commit() error
	Rollback() error
}

type SubscriptionTagStore interface {
	BeginSubscriptionTags(context.Context) (SubscriptionTagTransaction, error)
}
