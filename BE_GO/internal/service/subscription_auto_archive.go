package service

import (
	"context"
	"encoding/json"
	"log/slog"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

type SubscriptionAutoArchive struct {
	store  domain.SubscriptionAutoArchiveStore
	now    func() time.Time
	logger *slog.Logger
}

func NewSubscriptionAutoArchive(store domain.SubscriptionAutoArchiveStore, now func() time.Time, logger *slog.Logger) *SubscriptionAutoArchive {
	return &SubscriptionAutoArchive{store: store, now: now, logger: logger}
}
func (s *SubscriptionAutoArchive) Run(ctx context.Context, job domain.Job) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	var payload map[string]json.RawMessage
	if job.AssociationID != uuid.Nil || job.ID == uuid.Nil || job.LeaseToken == uuid.Nil || job.State != "running" || job.Kind != domain.JobAutoArchiveSubscription || job.Version != 1 || len(job.Payload) > 65536 || json.Unmarshal(job.Payload, &payload) != nil || payload == nil || len(payload) != 0 {
		return domain.ErrJobInvalid
	}
	tx, err := s.store.BeginSubscriptionArchive(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	// Freeze the execution instant, preserving the literal strict UTC-midnight
	// fiscal threshold. Custom subscription ends affect defaults, not eligibility.
	count, err := s.archive(ctx, tx, s.now().UTC())
	if err != nil {
		rollbackErr := tx.Rollback()
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if rollbackErr != nil {
			return rollbackErr
		}
		s.logger.WarnContext(ctx, "automatic subscription archive failed", "code", "subscription_archive_failed")
		completion, beginErr := s.store.BeginSubscriptionArchive(ctx)
		if beginErr != nil {
			return beginErr
		}
		defer completion.Rollback()
		if err = completion.CompleteJob(ctx, job.ID, job.LeaseToken); err != nil {
			return err
		}
		return completion.Commit()
	}
	if err = tx.CompleteJob(ctx, job.ID, job.LeaseToken); err != nil {
		return err
	}
	if err = tx.Commit(); err != nil {
		return err
	}
	s.logger.InfoContext(ctx, "subscriptions archived", "subscriptions", count)
	return nil
}
func (s *SubscriptionAutoArchive) archive(ctx context.Context, tx domain.SubscriptionArchiveTransaction, now time.Time) (int, error) {
	var cursor *uuid.UUID
	count := 0
	for {
		if err := ctx.Err(); err != nil {
			return 0, err
		}
		rows, err := tx.ReadArchivePage(ctx, cursor)
		if err != nil {
			return 0, err
		}
		if len(rows) == 0 {
			return count, nil
		}
		if len(rows) > domain.SubscriptionArchiveBatchLimit {
			return 0, domain.ErrPaymentSaveBatch
		}
		plans := make([]domain.SubscriptionArchivePlan, 0, len(rows))
		refs := make([]domain.PaymentSaveReference, 0, len(rows))
		for _, row := range rows {
			// The concrete reader supplies locked rows in increasing UUID order.
			if cursor != nil && row.ID.String() <= cursor.String() {
				return 0, domain.ErrPaymentSaveScope
			}
			id := row.ID
			cursor = &id
			period, e := SeasonRange(ctx, row.Created, row.Fiscal)
			if e != nil {
				return 0, e
			}
			if !now.After(period.End) {
				continue
			}
			dates, e := ResolveSubscriptionDates(ctx, row.Created, row.Rule, row.Dates)
			if e != nil {
				return 0, e
			}
			plans = append(plans, domain.SubscriptionArchivePlan{ID: row.ID, AssociationID: row.AssociationID, Dates: dates})
			if row.PaymentID != nil {
				refs = append(refs, domain.PaymentSaveReference{PaymentID: *row.PaymentID, AssociationID: row.AssociationID})
			}
		}
		if len(plans) == 0 {
			continue
		}
		refs, err = domain.UniquePaymentSaveReferences(refs)
		if err != nil {
			return 0, err
		}
		if err = tx.LockArchivePayments(ctx, refs); err != nil {
			return 0, err
		}
		payments, err := NewPaymentSavePreparation(tx).Prepare(ctx, refs)
		if err != nil {
			return 0, err
		}
		invoices := make([]domain.ArchiveInvoiceReference, 0, len(payments))
		seen := map[uuid.UUID]uuid.UUID{}
		for _, p := range payments {
			if invoice := p.Payment.Invoice; invoice != nil {
				if previous, ok := seen[invoice.ID]; ok {
					if previous != p.Payment.AssociationID {
						return 0, domain.ErrPaymentSaveScope
					}
					continue
				}
				seen[invoice.ID] = p.Payment.AssociationID
				invoices = append(invoices, domain.ArchiveInvoiceReference{ID: invoice.ID, AssociationID: p.Payment.AssociationID})
			}
		}
		if err = tx.ArchiveSubscriptions(ctx, plans, now); err != nil {
			return 0, err
		}
		if err = tx.ArchivePayments(ctx, payments, now); err != nil {
			return 0, err
		}
		if err = tx.ArchiveInvoices(ctx, invoices, now); err != nil {
			return 0, err
		}
		count += len(plans)
	}
}
