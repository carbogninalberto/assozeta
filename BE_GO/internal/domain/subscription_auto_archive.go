package domain

import (
	"context"
	"time"

	"github.com/google/uuid"
)

// JobAutoArchiveSubscription is the Celery name of auto_archive_subscription.
const JobAutoArchiveSubscription = "auto_archive_subscription"

// SubscriptionArchiveCandidate is one row of auto_archive_subscription's
// queryset: a live, unarchived subscription whose association owner enables
// auto_archive, with the owner settings Subscription.save and the balance
// year read, and the select_related payment and invoice keys.
type SubscriptionArchiveCandidate struct {
	ID        uuid.UUID
	PaymentID *uuid.UUID
	InvoiceID *uuid.UUID
	Created   time.Time
	Fiscal    CalendarRule
	Rule      SubscriptionDateRule
	Dates     SubscriptionDates
}

type SubscriptionAutoArchiveStore interface {
	// BeginSubscriptionArchive opens a READ COMMITTED transaction fenced by
	// the job lease; each Django autocommit step or atomic block is one.
	BeginSubscriptionArchive(context.Context, Job) (SubscriptionArchiveTransaction, error)
}

type SubscriptionArchiveTransaction interface {
	ArchiveCandidates(context.Context) ([]SubscriptionArchiveCandidate, error)
	// ArchiveSubscription is _archive_subscription: save(update_fields=
	// ['archived']) on the subscription, then its payment and invoice, each
	// audited by auditlog's pre_save diff restricted to archived.
	ArchiveSubscription(context.Context, SubscriptionArchiveCandidate) error
	CompleteJob(context.Context, uuid.UUID, uuid.UUID) error
	Commit() error
	Rollback() error
}
