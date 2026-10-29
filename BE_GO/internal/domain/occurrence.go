package domain

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"
)

var (
	ErrOccurrenceInactive = errors.New("Go does not own periodic publication")
	ErrOccurrenceBusy     = errors.New("periodic source or definition is being edited")
	ErrOccurrenceNotFound = errors.New("periodic source no longer exists")
)

// Source/count metadata belongs to publication, separately from the pure timing
// evaluator. Now is sampled from PostgreSQL after ownership and source locking.
type OccurrenceSnapshot struct {
	SourceID            int64
	SourceName          string
	TotalRunCount       int64
	OwnershipGeneration int64
	Now                 time.Time
	Schedule            PeriodicSchedule
}

type OccurrenceReceipt struct {
	ID                  uuid.UUID `db:"id"`
	SourceID            int64     `db:"periodic_task_id"`
	SourceName          string    `db:"source_name"`
	Sequence            int64     `db:"dispatch_sequence"`
	JobID               uuid.UUID `db:"job_id"`
	DispatchedAt        time.Time `db:"dispatched_at"`
	OwnershipGeneration int64     `db:"ownership_generation"`
	DefinitionDigest    string    `db:"definition_sha256"`
}

type OccurrenceStore interface {
	BeginOccurrence(context.Context, int64) (OccurrenceTransaction, error)
}

// Publication policy and due evaluation belong to the service. The repository
// never accepts arbitrary jobs/arguments: Publish records only daily reset v1.
// A receipt is durable only after Commit succeeds; every other outcome rolls back.
type OccurrenceTransaction interface {
	Snapshot() OccurrenceSnapshot
	Publish(context.Context) (OccurrenceReceipt, error)
	Commit() error
	Rollback() error
}

type OccurrenceResult struct {
	Published      bool
	NextCheckAfter time.Duration
	Receipt        OccurrenceReceipt
}
