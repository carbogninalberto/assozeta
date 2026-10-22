package domain

import (
	"context"
	"time"
)

type ProfileUpdatePerson struct {
	ID          string  `db:"user_id"`
	Role        int     `db:"role"`
	FirstName   string  `db:"first_name"`
	LastName    string  `db:"last_name"`
	Username    string  `db:"username"`
	AvatarImage *string `db:"avatar_image"`
}

type ProfileUpdateAssociation struct {
	ID, OwnerID string
	Values      map[string]any
}

type ProfileUpdateAudit struct {
	Model, ID, ObjectRepresentation string
	Changes                         map[string][2]string
	Context                         LoginAuditContext
	AssociationID, ResolutionPath   *string
	At                              time.Time
}

type ProfileUpdateImages interface {
	CompressProfileImage(context.Context, string) (string, error)
}

type ProfileUpdateStore interface {
	BeginProfileUpdate(context.Context) (ProfileUpdateTransaction, error)
}

type ProfileUpdateTransaction interface {
	ProfilePersonForUpdate(context.Context, string) (ProfileUpdatePerson, error)
	ProfileAssociationForUpdate(context.Context, string, *string) (ProfileUpdateAssociation, error)
	SetProfilePerson(context.Context, string, map[string]any) error
	SetProfileAssociation(context.Context, string, string, map[string]any) error
	AuditAssociations(context.Context, string, *string) (map[string]string, error)
	AppendProfileAudit(context.Context, ProfileUpdateAudit) error
	Commit() error
	Rollback() error
}
