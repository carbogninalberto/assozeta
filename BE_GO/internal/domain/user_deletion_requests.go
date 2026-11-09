package domain

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"
)

const JobDeleteUsersWithRequest = "delete_users_with_request"

// ErrUserDeletionSaveFailed reports that the UPDATE failed after the audit
// entry was written. Django's audit insert has already autocommitted at that
// point, so the transaction must be committed to keep it.
var ErrUserDeletionSaveFailed = errors.New("user deletion request save failed after its audit entry")

// UserDeletionCandidate is the in-memory User the Django task loaded before its
// loop: the email notice and the saved values derive from this snapshot.
type UserDeletionCandidate struct {
	ID        uuid.UUID
	Email     string
	Username  string
	FirstName string
	LastName  string
}

type UserDeletionRequestStore interface {
	BeginUserDeletionRequests(context.Context, Job) (UserDeletionRequestTransaction, error)
}

type UserDeletionRequestTransaction interface {
	// DueUsers selects active, not deleted users whose delete_on is set and on
	// or before the UTC date.
	DueUsers(context.Context, time.Time) ([]UserDeletionCandidate, error)
	// MarkDeleted applies the task's User.save(): suffixed email and username,
	// inactive, deleted, delete_on cleared, with the auditlog update entry.
	MarkDeleted(context.Context, UserDeletionCandidate, time.Time) error
	CompleteJob(context.Context, uuid.UUID, uuid.UUID) error
	Commit() error
	Rollback() error
}
