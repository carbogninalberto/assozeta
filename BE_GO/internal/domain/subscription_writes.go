package domain

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

var (
	// ErrSubscriptionEditDenied is PermissionDenied("User not allowed.").
	ErrSubscriptionEditDenied = errors.New("subscription edit not allowed")
	// ErrSubscriptionEditForeignReference is tenant_scope's generic PermissionDenied.
	ErrSubscriptionEditForeignReference = errors.New("subscription edit references another association")
	// ErrSubscriptionEditServer is an exception Django raises (the 500 page).
	ErrSubscriptionEditServer = errors.New("subscription edit server error")
	// ErrSubscriptionEditNotPorted refuses input this port does not handle,
	// before any write (the 500 page).
	ErrSubscriptionEditNotPorted = refusal.New("subscription edit input not ported")
)

// SubscriptionEditAssociate is the stored Associate row the edit reads and
// writes (the serializer's writable columns plus the lookup state).
type SubscriptionEditAssociate struct {
	ID            string     `db:"associate_id"`
	UserID        *string    `db:"user_id"`
	AssociationID *string    `db:"sport_association_id"`
	FirstName     *string    `db:"first_name"`
	LastName      *string    `db:"last_name"`
	Sex           string     `db:"sex"`
	TaxCode       *string    `db:"tax_code"`
	BornDate      *time.Time `db:"born_date"`
	BornCity      *string    `db:"born_city"`
	AddressCity   *string    `db:"address_city"`
	Address       *string    `db:"address"`
	AddressCap    *string    `db:"address_cap"`
	Email         *string    `db:"email"`
	Phone         *string    `db:"phone"`
	IsMinor       bool       `db:"is_minor"`
	GroupID       *string    `db:"group_id"`
	Deleted       bool       `db:"deleted"`
	FamilyID      *string    `db:"family_id"`
	FamilyRole    *string    `db:"family_role"`
	CreationDate  time.Time  `db:"creation_date"`
}

// SubscriptionEditRelation is an AssociateTutorRelation row.
type SubscriptionEditRelation struct {
	ID          int64  `db:"id"`
	AssociateID string `db:"associate_id"`
	TutorID     string `db:"tutor_id"`
	IsPrimary   bool   `db:"is_primary"`
}

// SubscriptionEditAudit is one auditlog entry; Created renders the stored row
// as a create diff (with the reverse accessors), otherwise Changes are used.
type SubscriptionEditAudit struct {
	Model       string // "associate" or "associatetutorrelation"
	Key         string // object_pk as the instance holds it (the spelling a client sent)
	Row         string // the stored row's canonical key when Key is a spelling of it
	Action      int
	Created     bool
	Changes     map[string][2]string
	Association *string
	Path        string
	Context     LoginAuditContext
	At          time.Time
}

type SubscriptionEditTransaction interface {
	// Associate is the live, group-aware Associate (group nil: unfiltered),
	// limited to association when non-empty; nil when absent. Locked.
	Associate(ctx context.Context, id string, group *string, association string) (*SubscriptionEditAssociate, error)
	// ActiveAssociationOf is SportAssociation.objects.filter(user=...).first().
	ActiveAssociationOf(ctx context.Context, user string) (*string, error)
	// AssociationExists covers every row (the serializer's default manager);
	// ActiveAssociationExists only live ones (SportAssociation.objects).
	AssociationExists(ctx context.Context, id string) (bool, error)
	ActiveAssociationExists(ctx context.Context, id string) (bool, error)
	FamilyExists(ctx context.Context, id string) (bool, error)
	GroupExists(ctx context.Context, id string) (bool, error)
	// TaxCodeMatches are the live, group-aware associates whose tax code
	// matches (iexact) in association (nil: no association), most recent
	// first. Locked.
	TaxCodeMatches(ctx context.Context, taxCode string, association, group *string) ([]SubscriptionEditAssociate, error)
	// Relations are the associate's tutor relations by key.
	Relations(ctx context.Context, associate string) ([]SubscriptionEditRelation, error)
	// NewestTaxCodeMatch is the query Django runs after MultipleObjectsReturned:
	// the same filter ordered by -creation_date, first row (the database breaks ties).
	NewestTaxCodeMatch(ctx context.Context, taxCode string, association, group *string) (*SubscriptionEditAssociate, error)
	// AssociateAny, AssociateByTaxCode, LiveTaxCodeTaken and SetAssociateDeleted are AssociateSerializer.create's associate_id branch
	// (all_objects() lookups, see SubscriptionRenewAssociateStore).
	AssociateAny(ctx context.Context, id string) (*SubscriptionEditAssociate, error)
	AssociateByTaxCode(ctx context.Context, taxCode, association string) (*SubscriptionEditAssociate, error)
	LiveTaxCodeTaken(ctx context.Context, taxCode, association, except string) (bool, error)
	SetAssociateDeleted(ctx context.Context, id string, deleted bool) error
	// OtherReferences reports a row that references the associate outside the tutor relations.
	OtherReferences(ctx context.Context, associate string) (bool, error)
	// DeleteTutorCollected is Associate.delete() through the Collector port (references, cascades and audit included).
	DeleteTutorCollected(ctx context.Context, tutor string, audit LoginAuditContext, group *string, now func() time.Time) error
	// AssociateSnapshot is the stored row as JSON; RestoreAssociate inserts it back with is_minor true (the stale
	// instance's save() after the row was deleted).
	AssociateSnapshot(ctx context.Context, associate string) (string, error)
	RestoreAssociate(ctx context.Context, snapshot string) error
	InsertAssociate(ctx context.Context, row SubscriptionEditAssociate) error
	// SaveAssociate writes the serializer's columns and is_minor (Model.save()).
	SaveAssociate(ctx context.Context, row SubscriptionEditAssociate) error
	PromoteRelation(ctx context.Context, id int64) error
	// DeleteTutor deletes the associate's tutor relations (both directions)
	// in reversed key order and then the associate, returning their delete
	// diffs in that order (relations first).
	DeleteTutor(ctx context.Context, tutor string) (relations []SubscriptionEditRelationDelete, associate map[string][2]string, err error)
	Audit(ctx context.Context, entry SubscriptionEditAudit) error
	Commit() error
	Rollback() error
}

type SubscriptionEditRelationDelete struct {
	ID      int64
	Changes map[string][2]string
}

type SubscriptionEditStore interface {
	BeginSubscriptionEdit(context.Context) (SubscriptionEditTransaction, error)
}
