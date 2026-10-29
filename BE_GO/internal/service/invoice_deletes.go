package service

import (
	"context"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type InvoiceDeletes struct {
	store domain.InvoiceDeleteStore
	now   func() time.Time
}

func NewInvoiceDeletes(store domain.InvoiceDeleteStore, now func() time.Time) *InvoiceDeletes {
	return &InvoiceDeletes{store: store, now: now}
}

// Delete follows invoice_suppliers_delete and invoice_customers_delete:
// is_valid_uuid (DRF 400), the impersonation-scoped association get (500
// when absent), then the association's invoice; a missing or foreign invoice
// raises a bare Exception (500).
func (s *InvoiceDeletes) Delete(ctx context.Context, identity domain.ResolvedIdentity, kind domain.InvoiceDeleteKind, raw string, audit domain.LoginAuditContext) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return err
	}
	tx, err := s.store.BeginInvoiceDelete(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	association, err := tx.AssociationForOwner(ctx, identity.ScopeUser.ID, paymentCategoryAssociationScope(identity))
	if err != nil {
		return err
	}
	if err = tx.DeleteInvoice(ctx, kind, id, association, paymentCategoryAuditContext(identity, audit),
		s.now().UTC().Truncate(time.Microsecond)); err != nil {
		return err
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	return tx.Commit()
}
