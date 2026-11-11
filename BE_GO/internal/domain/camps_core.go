package domain

import "context"

// Camp is one application_campsandretreats row.
type Camp struct {
	ID            string  `db:"camps_and_retreats_id"`
	Title         *string `db:"title"`
	Description   *string `db:"description"`
	AssociationID string  `db:"sport_association_id"`
}

// CampAssociationPublic is what CampsAndRetreatsPublicInfoSerializer renders
// of the camp's association.
type CampAssociationPublic struct {
	Denomination string `db:"denomination"`
	Username     string `db:"username"`
}

// CampsCoreTransaction adds the camp rows to the camps periods transaction.
type CampsCoreTransaction interface {
	CampsTransaction
	// Camps is CampsAndRetreats.objects.filter(sport_association=association).
	Camps(ctx context.Context, association string) ([]Camp, error)
	Camp(ctx context.Context, id string, lock bool) (*Camp, error)
	// AssociationRelated is the sport_association PrimaryKeyRelatedField
	// lookup (SportAssociation's default manager keeps deleted rows).
	AssociationRelated(ctx context.Context, id string) (CampsRelated, error)
	AssociationPublic(ctx context.Context, association string) (CampAssociationPublic, error)
	InsertCamp(ctx context.Context, row Camp) error
	UpdateCamp(ctx context.Context, row Camp) error
	// CampPeriods is the camp's periods ordered by start_date.
	CampPeriods(ctx context.Context, camp string) ([]CampsPeriod, error)
	// DeleteCamp runs the collector delete of the camp and its cascades,
	// returning each deleted row's auditlog changes.
	DeleteCamp(ctx context.Context, id string) ([]CampsAudit, error)
}

type CampsCoreStore interface {
	BeginCampsCore(context.Context) (CampsCoreTransaction, error)
}
