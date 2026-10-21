package domain

import (
	"context"
	"time"

	"github.com/google/uuid"
)

const JobAutoArchiveSubscription = "auto_archive_subscription"
const SubscriptionArchiveBatchLimit = 100

type SubscriptionArchiveCandidate struct {
	ID, AssociationID uuid.UUID
	PaymentID         *uuid.UUID
	Created           time.Time
	Fiscal            CalendarRule
	Rule              SubscriptionDateRule
	Dates             SubscriptionDates
}
type SubscriptionArchivePlan struct {
	ID, AssociationID uuid.UUID
	Dates             SubscriptionDates
}
type ArchiveInvoiceReference struct{ ID, AssociationID uuid.UUID }
type SubscriptionAutoArchiveStore interface {
	BeginSubscriptionArchive(context.Context) (SubscriptionArchiveTransaction, error)
}
type SubscriptionArchiveTransaction interface {
	PaymentSaveInputReader
	ReadArchivePage(context.Context, *uuid.UUID) ([]SubscriptionArchiveCandidate, error)
	LockArchivePayments(context.Context, []PaymentSaveReference) error
	ArchiveSubscriptions(context.Context, []SubscriptionArchivePlan, time.Time) error
	ArchivePayments(context.Context, []PaymentSavePlan, time.Time) error
	ArchiveInvoices(context.Context, []ArchiveInvoiceReference, time.Time) error
	CompleteJob(context.Context, uuid.UUID, uuid.UUID) error
	Commit() error
	Rollback() error
}
