package domain

import (
	"context"
	"time"
)

// SearchProfileUser is the active user search_profile finds by username.
type SearchProfileUser struct {
	ID              string  `db:"user_id"`
	Role            int     `db:"role"`
	FirstName       string  `db:"first_name"`
	LastName        string  `db:"last_name"`
	Username        string  `db:"username"`
	Avatar          *string `db:"avatar_image"`
	DisableCreation bool    `db:"disable_account_creation"`
	ForceCreation   bool    `db:"force_account_creation"`
	OnlinePayments  bool    `db:"online_payments"`
	StartMonth      int     `db:"subscription_start_month"`
	StartDay        int     `db:"subscription_start_day"`
}

// SearchProfileModule is the association's module fields the view appends.
type SearchProfileModule struct {
	Demand                   *string `db:"demand"`
	Regulation               *string `db:"regulation"`
	AdditionalSections       string  `db:"additional_sections"`
	ShowRegulationToMembers  bool    `db:"show_regulation_to_members"`
	ShowRegulationToBoth     bool    `db:"show_regulation_to_both"`
	ShowRegulationToAthletes bool    `db:"show_regulation_to_athletes"`
	ShowDemandToMembers      bool    `db:"show_demand_to_members"`
	ShowDemandToBoth         bool    `db:"show_demand_to_both"`
	ShowDemandToAthletes     bool    `db:"show_demand_to_athletes"`
}

// SearchProfileCourse is one active course with the columns CourseSerializer
// renders; JSON columns are text, Tags/Locations aggregated JSON arrays.
type SearchProfileCourse struct {
	ID                     string     `db:"course_id"`
	AssociationID          *string    `db:"sport_association_id"`
	StatusFlag             int        `db:"status_flag"`
	CourseType             int        `db:"course_type"`
	CreationDate           time.Time  `db:"creation_date"`
	Title                  *string    `db:"title"`
	Description            *string    `db:"description"`
	MultiPayments          bool       `db:"multi_payments"`
	Fee                    *string    `db:"fee"`
	Events                 *string    `db:"events"`
	OneFeePayment          bool       `db:"one_fee_payment"`
	OneFee                 string     `db:"one_fee"`
	GoogleCalendarID       *string    `db:"google_calendar_id"`
	GoogleBackgroundColor  *string    `db:"google_background_color"`
	Tags                   string     `db:"tags"`
	MultipleQuotes         *string    `db:"multiple_quotes"`
	Locations              string     `db:"locations"`
	StartDate              *time.Time `db:"start_date"`
	EndDate                *time.Time `db:"end_date"`
	Pinned                 bool       `db:"pinned"`
	BilledDurationIsSeason bool       `db:"billed_duration_is_sport_season"`
	BilledFrequency        *int       `db:"billed_frequency"`
	BilledFromSubscription bool       `db:"billed_from_subscription_date"`
	BilledFromDayOfMonth   int        `db:"billed_from_day_of_month"`
	AutoRenewal            bool       `db:"auto_renewal"`
	Deleted                bool       `db:"deleted"`
	GroupID                *string    `db:"group_id"`
	TotalSubscriptions     int64      `db:"total_subscriptions"`
}

type SearchProfileTag struct {
	ID   string `db:"tag_id" json:"tag_id"`
	Name string `db:"tag_name" json:"tag_name"`
}

type SearchProfileStore interface {
	// User is User.objects.filter(username__iexact=...).first() (by pk).
	User(ctx context.Context, username string) (*SearchProfileUser, error)
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	Module(ctx context.Context, association string) (SearchProfileModule, error)
	// Courses lists live ACTIVE courses of one pinned state, newest first.
	Courses(ctx context.Context, association string, pinned bool) ([]SearchProfileCourse, error)
	Carnets(ctx context.Context, association string) ([]CarnetListCarnet, error)
	Tags(ctx context.Context, association string) ([]SearchProfileTag, error)
}
