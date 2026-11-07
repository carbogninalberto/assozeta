package domain

import (
	"context"
	"time"
)

// CampSubscription is one application_campsandretreatssubscription row with
// its camp's association.
type CampSubscription struct {
	ID                string    `db:"camps_and_retreats_subscription_id"`
	CampID            string    `db:"camps_and_retreats_id"`
	CampAssociationID string    `db:"camp_association_id"`
	Status            int64     `db:"status_flag"`
	RegistrationID    *string   `db:"subscription_id"`
	CreatedAt         time.Time `db:"created_at"`
}

// CampRegistration is the Subscription a camp subscription enrolls, with its
// associate's user and family.
type CampRegistration struct {
	ID              string  `db:"subscription_id"`
	AssociationID   string  `db:"sport_association_id"`
	UserID          string  `db:"user_id"`
	AssociateID     *string `db:"associate_id"`
	Archived        bool    `db:"archived"`
	Deleted         bool    `db:"deleted"`
	GroupID         *string `db:"group_id"`
	AssociateUserID *string `db:"associate_user_id"`
	FamilyID        *string `db:"family_id"`
}

// CampRegistrationBasic is SubscriptionBasicSerializer's data source.
type CampRegistrationBasic struct {
	ID          string     `db:"subscription_id"`
	StartDate   *time.Time `db:"start_date"`
	EndDate     *time.Time `db:"end_date"`
	AssociateID *string    `db:"associate_id"`
	FirstName   *string    `db:"first_name"`
	LastName    *string    `db:"last_name"`
	TaxCode     *string    `db:"tax_code"`
	FamilyID    *string    `db:"family_id"`
	FamilyRole  *string    `db:"family_role"`
}

// CampSubscriptionPeriod is one subscription period with its link rows.
type CampSubscriptionPeriod struct {
	ID             string  `db:"camps_and_retreats_subscription_period_id"`
	PeriodID       string  `db:"camps_and_retreats_period_id"`
	SubscriptionID string  `db:"camps_and_retreats_subscription_id"`
	PaymentID      *string `db:"payment_id"`
	Services       []string
}

// CampGenerateRow is one subscription period of generate_payment_for_periods
// with its period (select_related).
type CampGenerateRow struct {
	ID                string  `db:"camps_and_retreats_subscription_period_id"`
	PeriodTitle       *string `db:"title"`
	PeriodDescription *string `db:"description"`
	PeriodFee         string  `db:"fee"`
}

// CampSelectedService is one service of a subscription period's
// camps_and_retreats_period_services.
type CampSelectedService struct {
	ID         string  `db:"camps_and_retreats_period_service_id"`
	Title      *string `db:"title"`
	Fee        string  `db:"fee"`
	CategoryID *string `db:"payment_category_id"`
}

// CampPaymentCreate is the Payment.objects.create() of create_payment after
// Payment.save()'s defaults.
type CampPaymentCreate struct {
	ID            string
	Amount        string
	AmountRepr    string
	CreatedAt     time.Time
	Description   *string
	CategoryID    *string
	Metas         string
	MetasAudit    string
	UserID        string
	AssociateID   *string
	AssociationID string
	AccountID     *string
	Notes         string
	GroupID       *string
}

// CampsSubscriptionsTransaction adds the camp subscription rows to the camps
// periods transaction.
type CampsSubscriptionsTransaction interface {
	CampsTransaction
	CampSubscriptions(ctx context.Context, association, camp string) ([]CampSubscription, error)
	CampSubscription(ctx context.Context, id string, lock bool) (*CampSubscription, error)
	CampSubscriptionIDTaken(ctx context.Context, id string) (bool, error)
	// RegistrationRelated is Subscription.objects (group-aware) by pk.
	RegistrationRelated(ctx context.Context, id string, group *string) (*CampRegistration, error)
	RegistrationBasic(ctx context.Context, id string, group *string) (*CampRegistrationBasic, error)
	CampOwnerUser(ctx context.Context, camp string) (string, error)
	CampTitle(ctx context.Context, camp string) (*string, error)
	FamilyOwned(ctx context.Context, user, association, family string, group *string) (bool, error)
	PeriodOfCamp(ctx context.Context, period, camp string) (bool, error)
	ServiceOfPeriod(ctx context.Context, service, period string) (bool, error)
	PeriodExists(ctx context.Context, id string) (bool, error)
	ExistingServices(ctx context.Context, ids []string) ([]string, error)
	GroupExists(ctx context.Context, id string) (bool, error)
	DefaultBankAccount(ctx context.Context, association string) (*string, error)
	InsertCampSubscription(ctx context.Context, row CampSubscription) error
	InsertSubscriptionPeriod(ctx context.Context, id, period, subscription string) error
	// LinkSubscriptionPeriod is ManyRelatedManager.add() then save().
	LinkSubscriptionPeriod(ctx context.Context, id string, services []string) error
	SubscriptionPeriods(ctx context.Context, subscription string) ([]CampSubscriptionPeriod, error)
	UnpaidSubscriptionPeriods(ctx context.Context, subscription string) ([]CampSubscriptionPeriod, error)
	PaidPeriodIDs(ctx context.Context, subscription string) ([]string, error)
	GenerateRows(ctx context.Context, subscription string) ([]CampGenerateRow, error)
	SelectedServices(ctx context.Context, subscriptionPeriod string) ([]CampSelectedService, error)
	PeriodServicesUnordered(ctx context.Context, period string) ([]CampsService, error)
	CreatePayment(ctx context.Context, row CampPaymentCreate) (map[string][2]string, error)
	SetSubscriptionPeriodPayment(ctx context.Context, id, payment string) error
	// DeletePayment is payment.delete() (signal, SET_NULL, join rows) with its
	// audit entry.
	DeletePayment(ctx context.Context, id string, audit CourseSubscriptionAudit) error
	// SubscriptionPeriodChanges is auditlog's delete diff of the stored row
	// (the in-memory instance a later delete renders).
	SubscriptionPeriodChanges(ctx context.Context, id string) (map[string][2]string, error)
	// DeleteSubscriptionPeriod deletes the link rows and the row; nil changes
	// render the stored row.
	DeleteSubscriptionPeriod(ctx context.Context, id string, changes map[string][2]string) (CampsAudit, error)
	DeleteCampSubscription(ctx context.Context, id string) ([]CampsAudit, error)
	ListPayment(ctx context.Context, id string, group *string) (CourseSubscriptionPayment, error)
	PaidPeriodsOf(ctx context.Context, associate *string, group *string) ([]string, error)
}

type CampsSubscriptionsStore interface {
	BeginCampsSubscriptions(context.Context) (CampsSubscriptionsTransaction, error)
}
