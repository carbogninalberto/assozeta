package domain

import (
	"context"
	"time"
)

// CampsPeriod is one application_campsandretreatsperiod row with its camp's
// association.
type CampsPeriod struct {
	ID                string    `db:"camps_and_retreats_period_id"`
	CampID            string    `db:"camps_and_retreats_id"`
	CampAssociationID string    `db:"camp_association_id"`
	Start             time.Time `db:"start_date"`
	End               time.Time `db:"end_date"`
	Title             *string   `db:"title"`
	Description       *string   `db:"description"`
	Fee               string    `db:"fee"`
	Max               *int64    `db:"max_participants"`
	CreatedAt         time.Time `db:"created_at"`
}

// CampsService is one application_campsandretreatsperiodsservice row with its
// period's camp association.
type CampsService struct {
	ID                string    `db:"camps_and_retreats_period_service_id"`
	PeriodID          string    `db:"camps_and_retreats_period_id"`
	CampAssociationID string    `db:"camp_association_id"`
	Title             *string   `db:"title"`
	Description       *string   `db:"description"`
	Fee               string    `db:"fee"`
	CategoryID        *string   `db:"payment_category_id"`
	CreatedAt         time.Time `db:"created_at"`
	Category          *PaymentCategoryState
}

// CampsRelated is a related row found by pk with the association the tenant
// scope resolves for it (nil when none).
type CampsRelated struct {
	Found         bool
	AssociationID *string
}

// CampsWalkRow is one subscription period of the walk with its unpaid payment.
type CampsWalkRow struct {
	SubscriptionPeriodID string  `db:"camps_and_retreats_subscription_period_id"`
	PeriodTitle          *string `db:"period_title"`
	PeriodFee            string  `db:"period_fee"`
	CampTitle            *string `db:"camp_title"`
	CampDescription      *string `db:"camp_description"`
	Payment              CampsPayment
}

// CampsPayment is the payment columns the walk's Payment.save() reads.
type CampsPayment struct {
	PaymentSaveRow
	AssociationNull bool    `db:"association_null"`
	Amount          string  `db:"amount"`
	Notes           *string `db:"notes"`
	Metas           *string `db:"meta_payment_categories"`
	Deleted         bool    `db:"deleted"`
	GroupID         *string `db:"group_id"`
}

// CampsAudit is one auditlog entry with its index (nil association: none).
type CampsAudit struct {
	Model         string
	Key           string
	Action        int
	Changes       map[string][2]string
	AssociationID *string
	Path          string
	// Full marks an update entry of a payment its manager does not return:
	// every field of the saved row against None. Changes then holds only the
	// values the instance renders differently from the stored row.
	Full bool
}

type CampsTransaction interface {
	// OwnerAssociation is SportAssociation.objects.get(user=owner).
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	// ActorAssociation is SportAssociation.objects.filter(user=actor).first().
	ActorAssociation(ctx context.Context, actor string) (*string, error)
	Period(ctx context.Context, id string, lock bool) (*CampsPeriod, error)
	PeriodServiceIDs(ctx context.Context, period string) ([]string, error)
	PeriodIDTaken(ctx context.Context, id string) (bool, error)
	CampRelated(ctx context.Context, id string) (CampsRelated, error)
	PeriodRelated(ctx context.Context, id string) (CampsRelated, error)
	ServiceRelated(ctx context.Context, id string) (CampsRelated, error)
	CategoryRelated(ctx context.Context, id string) (CampsRelated, error)
	InsertPeriod(ctx context.Context, row CampsPeriod) error
	UpdatePeriod(ctx context.Context, row CampsPeriod) error
	// LinkPeriodServices applies ManyRelatedManager.set's remove then add.
	LinkPeriodServices(ctx context.Context, period string, remove, add []string) error
	PeriodServices(ctx context.Context, period string) ([]CampsService, error)
	Service(ctx context.Context, id string, lock bool) (*CampsService, error)
	ServiceIDTaken(ctx context.Context, id string) (bool, error)
	InsertService(ctx context.Context, row CampsService) error
	UpdateService(ctx context.Context, row CampsService) error
	WalkRows(ctx context.Context, period string) ([]CampsWalkRow, error)
	SavePayment(ctx context.Context, row CampsPayment) error
	DefaultPaymentCategory(ctx context.Context, association *string) (id, name *string, err error)
	PaymentLinkedName(ctx context.Context, payment string) (PaymentLinkedName, error)
	// DeletePeriod deletes the period, its services and subscription periods
	// and their link rows, returning each deleted row's auditlog changes.
	DeletePeriod(ctx context.Context, id string) ([]CampsAudit, error)
	DeleteService(ctx context.Context, id string) (CampsAudit, error)
	AppendAudit(ctx context.Context, event CampsAudit, audit LoginAuditContext, at time.Time) error
	Commit() error
	Rollback() error
}

type CampsStore interface {
	BeginCamps(context.Context) (CampsTransaction, error)
}
