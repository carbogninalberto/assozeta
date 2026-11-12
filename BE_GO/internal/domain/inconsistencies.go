package domain

import (
	"context"
	"time"
)

// InconsistencyAssociate is one associate of the scope user with the fields
// AssociateSerializer renders and its tutor-relation state.
type InconsistencyAssociate struct {
	ID            string     `db:"associate_id"`
	Address       *string    `db:"address"`
	AddressCap    *string    `db:"address_cap"`
	AddressCity   *string    `db:"address_city"`
	BornCity      *string    `db:"born_city"`
	BornDate      *time.Time `db:"born_date"`
	Email         *string    `db:"email"`
	Sex           string     `db:"sex"`
	FirstName     *string    `db:"first_name"`
	LastName      *string    `db:"last_name"`
	IsMinor       bool       `db:"is_minor"`
	AssociationID *string    `db:"sport_association_id"`
	TaxCode       *string    `db:"tax_code"`
	UserID        *string    `db:"user_id"`
	OwnerUserID   *string    `db:"association_user_id"`
	Phone         *string    `db:"phone"`
	FamilyID      *string    `db:"family_id"`
	FamilyRole    *string    `db:"family_role"`
	HasPrimary    bool       `db:"has_primary"`
	// FirstRelation is the lowest-key relation when none is primary.
	FirstRelation *int64 `db:"first_relation_id"`
}

type TutorPromotionAudit struct {
	RelationID    int64
	Context       LoginAuditContext
	AssociationID *string
	Path          *string
	At            time.Time
}

type InconsistencyStore interface {
	BeginInconsistencies(context.Context) (InconsistencyTransaction, error)
}

type InconsistencyTransaction interface {
	// Associates lists non-deleted associates of user, optionally within a
	// group and an impersonation association.
	Associates(ctx context.Context, user string, group, scope *string) ([]InconsistencyAssociate, error)
	PromoteTutorRelation(ctx context.Context, relation int64) error
	ActiveAssociationOf(ctx context.Context, user string) (*string, error)
	AppendTutorPromotionAudit(context.Context, TutorPromotionAudit) error
	Commit() error
	Rollback() error
}
