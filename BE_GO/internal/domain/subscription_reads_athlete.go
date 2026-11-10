package domain

import (
	"context"
	"time"
)

// SubscriptionReadsSearchAssociation is what SportAssociationSearchSerializer
// reads: the association (base manager) and its owner.
type SubscriptionReadsSearchAssociation struct {
	ID              string  `db:"sport_association_id"`
	Denomination    string  `db:"denomination"`
	TaxCode         string  `db:"tax_code"`
	Address         *string `db:"address"`
	AddressCap      *string `db:"address_cap"`
	AddressCity     *string `db:"address_city"`
	OwnerRole       int     `db:"owner_role"`
	OwnerFirstName  string  `db:"owner_first_name"`
	OwnerLastName   string  `db:"owner_last_name"`
	OwnerUsername   string  `db:"owner_username"`
	OwnerAvatar     *string `db:"owner_avatar_image"`
	DisableCreation bool    `db:"owner_disable_account_creation"`
	ForceCreation   bool    `db:"owner_force_account_creation"`
	OnlinePayments  bool    `db:"owner_online_payments"`
	// OwnerAssociations counts SportAssociation.objects (live) of the owner.
	OwnerAssociations int64 `db:"owner_associations"`
}

// SubscriptionReadsAthleteCourse is one live course subscription with its course.
type SubscriptionReadsAthleteCourse struct {
	SubscriptionID string    `db:"subscription_id"`
	ID             string    `db:"course_subscription_id"`
	Paid           bool      `db:"paid"`
	CreationDate   time.Time `db:"creation_date"`
	OneFeePayment  bool      `db:"one_fee_payment"`
	CourseID       string    `db:"course_id"`
	CourseTitle    *string   `db:"course_title"`
	CourseOneFee   bool      `db:"course_one_fee_payment"`
	CourseFee      string    `db:"course_one_fee"`
}

// SubscriptionReadsAthleteStore is what get_optimized_subscriptions reads
// beyond the shared subscription rows.
type SubscriptionReadsAthleteStore interface {
	SearchAssociation(ctx context.Context, association string) (*SubscriptionReadsSearchAssociation, error)
	// AssociationOwner is the user of an associate's association (base manager); nil without one.
	AssociationOwner(ctx context.Context, association string) (*string, error)
	// AthleteCourses are CourseSubscription.objects (live) of the subscriptions, in heap order.
	AthleteCourses(ctx context.Context, subscriptions []string) ([]SubscriptionReadsAthleteCourse, error)
	// Carnets are the subscriptions with a CarnetSubscription.
	Carnets(ctx context.Context, subscriptions []string) (map[string]bool, error)
}
