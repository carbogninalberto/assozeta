package domain

import (
	"context"
	"time"
)

// ProfileAssociate is one Associate row rendered by profile/associates/*.
type ProfileAssociate struct {
	ID        string  `db:"associate_id"`
	FirstName *string `db:"first_name"`
	LastName  *string `db:"last_name"`
	IsMinor   bool    `db:"is_minor"`
}

type ProfileAssociateSubscription struct {
	SubscriptionID string `db:"subscription_id"`
	AssociateID    string `db:"associate_id"`
}

// ProfileAssociateScope is the managers' X-Group-Id filter plus
// scoped_queryset: with an impersonation association every queryset is bound
// to it, and an athlete person adds the family visibility boundary.
type ProfileAssociateScope struct {
	Group       *string
	Association *string
	Person      string
	Family      bool
}

type ProfileAssociatesReader interface {
	// CourseAssociation is Course.objects.filter(course_id=...).first():
	// found, then its association and the association's owner (both nil
	// when the course has none).
	CourseAssociation(ctx context.Context, course string, scope ProfileAssociateScope) (bool, *string, *string, error)
	// Associates of the given users (Associate.objects), optionally only enabled ones.
	Associates(ctx context.Context, users []string, enabledOnly bool, scope ProfileAssociateScope) ([]ProfileAssociate, error)
	// CourseSubscribed are the associate ids with a non-deleted course subscription.
	CourseSubscribed(ctx context.Context, course string, associates []string, scope ProfileAssociateScope) ([]string, error)
	// UserSubscriptions are Subscription.objects rows of user in association.
	UserSubscriptions(ctx context.Context, user, association string, associates []string, scope ProfileAssociateScope) ([]ProfileAssociateSubscription, error)
	// SeasonSubscriptions are the lowest-id subscription of each associate in
	// association created in [from, to].
	SeasonSubscriptions(ctx context.Context, association string, associates []string, from, to time.Time, scope ProfileAssociateScope) ([]ProfileAssociateSubscription, error)
	BalanceStart(ctx context.Context, user string) (int, int, error)
	// Avatar is User.objects.get(user_id=...).avatar_image (found false: DoesNotExist).
	Avatar(ctx context.Context, user string) (bool, *string, error)
}

// OnboardingAssociation holds the onboarding serializer's fields; fees are
// the stored numeric text.
type OnboardingAssociation struct {
	ID              string  `db:"sport_association_id"`
	Deleted         bool    `db:"deleted"`
	Denomination    string  `db:"denomination"`
	TaxCode         string  `db:"tax_code"`
	Logo            *string `db:"logo"`
	Sport           *string `db:"sport"`
	SubscriptionFee string  `db:"subscription_fee"`
	MembershipFee   *string `db:"membership_fee"`
}

type OnboardingUser struct {
	ID                     string  `db:"user_id"`
	FirstName              string  `db:"first_name"`
	LastName               string  `db:"last_name"`
	BalanceSheetYear       int64   `db:"balance_sheet_year"`
	BalanceSheetStartDay   int64   `db:"balance_sheet_start_day"`
	BalanceSheetStartMonth int64   `db:"balance_sheet_start_month"`
	SubscriptionStartDay   int64   `db:"subscription_start_day"`
	SubscriptionStartMonth int64   `db:"subscription_start_month"`
	Role                   *string `db:"lead_sport_association_role"`
	Size                   *string `db:"lead_sport_association_size"`
	Channel                *string `db:"lead_sport_market_channel"`
	Phone                  *string `db:"phone"`
}

type OnboardingFlags struct {
	ID    string `db:"users_onboarding_id"`
	Flags map[string]bool
}

type OnboardingConfiguration struct {
	ID        int64      `db:"id"`
	Completed *time.Time `db:"onboarding_completed_at"`
	OwnerID   string     `db:"owner_id"`
}

type ProfileMiscAudit struct {
	Model, Key, Representation string
	Changes                    map[string][2]string
	Context                    LoginAuditContext
	AssociationID              *string
	ResolutionPath             string
	At                         time.Time
}

type OnboardingStore interface {
	BeginOnboarding(ctx context.Context) (OnboardingTransaction, error)
}

// OnboardingTransaction is one Django autocommit step or the membership
// transaction.atomic() block.
type OnboardingTransaction interface {
	// Association is user.sportassociation (any deleted state); nil when none.
	Association(ctx context.Context, owner string) (*OnboardingAssociation, error)
	SaveAssociation(ctx context.Context, value OnboardingAssociation) error
	// FreshConfiguration is the first self-hosted fresh InstanceConfiguration
	// whose primary association is the given one, locked.
	FreshConfiguration(ctx context.Context, association string) (*OnboardingConfiguration, error)
	CompleteConfiguration(ctx context.Context, id int64, at time.Time) error
	User(ctx context.Context, id string) (OnboardingUser, error)
	SaveUser(ctx context.Context, value OnboardingUser) error
	// Flags are up to two UsersOnboarding rows of the user (objects.get).
	Flags(ctx context.Context, user string) ([]OnboardingFlags, error)
	SaveFlags(ctx context.Context, value OnboardingFlags) error
	// AssociationOf is SportAssociation.objects.filter(user=...).first().
	AssociationOf(ctx context.Context, user string) (*string, error)
	AppendAudit(ctx context.Context, event ProfileMiscAudit) error
	Commit() error
	Rollback() error
}
