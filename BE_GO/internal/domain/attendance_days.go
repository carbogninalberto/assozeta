package domain

import (
	"context"
	"errors"
	"time"
)

var ErrAttendanceDayNotFound = errors.New("error: attendance day not found.")

type AttendanceDayStore interface {
	BeginAttendanceDay(context.Context) (AttendanceDayTransaction, error)
}

type AttendanceDayTransaction interface {
	// OwnedAttendanceDay checks the day's course belongs to owner (and to scope, when set).
	OwnedAttendanceDay(ctx context.Context, id, owner string, scope *string) error
	ActiveAssociationOf(ctx context.Context, user string) (*string, error)
	DeleteAttendanceDay(ctx context.Context, id string, actorAssociation *string, audit LoginAuditContext, at time.Time) error
	Commit() error
	Rollback() error
}
