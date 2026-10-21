package domain

import (
	"context"
	"time"

	"github.com/google/uuid"
)

const JobUpdateAssociateAge = "update_age_of_associates"

type AssociateAgeUpdateStore interface {
	BeginAssociateAgeUpdate(context.Context) (AssociateAgeUpdateTransaction, error)
}

type AssociateAgeUpdateTransaction interface {
	PromoteAdultsWithAudit(context.Context, time.Time, time.Time) (int64, error)
	CompleteJob(context.Context, uuid.UUID, uuid.UUID) error
	Commit() error
	Rollback() error
}
