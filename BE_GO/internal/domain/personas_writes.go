package domain

import (
	"context"
	"errors"
	"time"
)

var (
	// ErrPersonaWriteDenied is PermissionDenied("User not allowed.").
	ErrPersonaWriteDenied = errors.New("persona write not allowed")
	// ErrPersonaWriteForeignReference is tenant_scope's generic PermissionDenied.
	ErrPersonaWriteForeignReference = errors.New("persona write references another association")
	// ErrPersonaRecoverMissing is recover's Response(status=404) without a body.
	ErrPersonaRecoverMissing = errors.New("persona to recover not found")
	// ErrPersonaUniqueViolation is the IntegrityError of a write the database rejected.
	ErrPersonaUniqueViolation = errors.New("persona write violates a unique index")
)

// PersonaRelationRow is an AssociateTutorRelation row.
type PersonaRelationRow struct {
	ID          int64  `db:"id"`
	AssociateID string `db:"associate_id"`
	TutorID     string `db:"tutor_id"`
	IsPrimary   bool   `db:"is_primary"`
}

// PersonaWriteAudit is one auditlog entry. Created renders the stored row as
// auditlog's create diff (with the reverse accessors); otherwise Changes.
type PersonaWriteAudit struct {
	Model       string // "associate" or "associatetutorrelation"
	Key         string
	Action      int
	Created     bool
	Changes     map[string][2]string
	Association *string
	Path        string
	Context     LoginAuditContext
	At          time.Time
	// SentGroup is the X-Group-Id as sent, shown by the create entry instead of the stored uuid.
	SentGroup string
}

type PersonaWritesTransaction interface {
	// Associate reads one row through the base manager (live nil: any state),
	// or through Associate.objects (live, group-aware) when live is set.
	Associate(ctx context.Context, id string, live bool, group *string) (*PersonaAssociate, error)
	// ActiveAssociationOf is SportAssociation.objects.filter(user=...).first().
	ActiveAssociationOf(ctx context.Context, user string) (*string, error)
	ActiveAssociationExists(ctx context.Context, id string) (bool, error)
	// ActiveUserExists is User.objects (deleted=False).
	ActiveUserExists(ctx context.Context, id string) (bool, error)
	// GroupAssociation is the group's association (found false: no group).
	GroupAssociation(ctx context.Context, id string) (association string, found bool, err error)
	FamilyExists(ctx context.Context, id string) (bool, error)
	// InsertAssociate and SaveAssociate (every column, Model.save()) return
	// ErrPersonaUniqueViolation, rolled back to before the statement.
	InsertAssociate(ctx context.Context, row PersonaAssociate) error
	SaveAssociate(ctx context.Context, row PersonaAssociate) error
	// Relations are the associate's tutor relations by key.
	Relations(ctx context.Context, associate string) ([]PersonaRelationRow, error)
	// DeleteRelation deletes one relation, returning its delete diff.
	DeleteRelation(ctx context.Context, id int64) (map[string][2]string, error)
	// InsertRelation and SetRelationPrimary return ErrPersonaUniqueViolation
	// (unique_primary_tutor), rolled back with the entry written in step.
	InsertRelation(ctx context.Context, associate, tutor string, primary bool, audit func(id int64) error) (int64, error)
	SetRelationPrimary(ctx context.Context, id int64, primary bool, audit func() error) error
	// BulkDelete is Associate.objects.filter(associate_id__in=ids,
	// sport_association=...).update(deleted=True) (live, group-aware).
	BulkDelete(ctx context.Context, ids []string, association string, group *string) error
	Audit(ctx context.Context, entry PersonaWriteAudit) error
	Commit() error
	Rollback() error
}

type PersonaWritesStore interface {
	BeginPersonaWrites(context.Context) (PersonaWritesTransaction, error)
}
