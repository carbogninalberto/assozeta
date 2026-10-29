package domain

import (
	"context"
	"errors"
	"time"
)

var (
	// ErrCourseLocationNotFound is get_object_or_404's
	// {'detail': 'No CourseLocation matches the given query.'}.
	ErrCourseLocationNotFound = errors.New("course location not found")
	// ErrCourseLocationMalformed is the bare Http404 for a pk the UUIDField
	// rejects ({'detail': 'Not found.'}).
	ErrCourseLocationMalformed = errors.New("course location pk malformed")
	// ErrCourseLocationForeignReference is the tenant scope's PermissionDenied
	// for a sport_association of another tenant.
	ErrCourseLocationForeignReference = errors.New("course location foreign association")
)

// CourseLocation is one application_courselocation row.
type CourseLocation struct {
	ID            string
	Title         *string
	Description   *string
	Address       *string
	AssociationID *string
}

type CourseLocationWriteTransaction interface {
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	// Location is the association's location, locked for the write.
	Location(ctx context.Context, id, association string) (*CourseLocation, error)
	LocationIDTaken(ctx context.Context, id string, exclude *string) (bool, error)
	AssociationExists(ctx context.Context, id string) (bool, error)
	CreateLocation(ctx context.Context, row CourseLocation, audit LoginAuditContext, at time.Time) error
	SaveLocation(ctx context.Context, row CourseLocation, changes map[string][2]string, audit LoginAuditContext, at time.Time) error
	DeleteLocation(ctx context.Context, id string, audit LoginAuditContext, at time.Time) error
	// LocationDocuments is the nested documents JSON of CourseLocationSerializer.
	LocationDocuments(ctx context.Context, id string) (string, error)
	Commit() error
	Rollback() error
}

type CourseLocationWriteStore interface {
	BeginCourseLocationWrite(context.Context) (CourseLocationWriteTransaction, error)
}
