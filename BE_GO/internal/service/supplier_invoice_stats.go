package service

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

var errSupplierInvoiceAmount = errors.New("supplier invoice amount is not numeric")

type SupplierInvoiceStats struct {
	store    domain.SupplierInvoiceStatsStore
	now      func() time.Time
	location *time.Location
}

func NewSupplierInvoiceStats(store domain.SupplierInvoiceStatsStore, now func() time.Time, location *time.Location) *SupplierInvoiceStats {
	return &SupplierInvoiceStats{store: store, now: now, location: location}
}

// Stats follows invoice_suppliers_stats: Python sums over the association's
// invoices and the unpaid ones due by naive datetime.now() + 30 days.
func (s *SupplierInvoiceStats) Stats(ctx context.Context, identity domain.ResolvedIdentity) (dto.SupplierInvoiceStats, error) {
	if err := ctx.Err(); err != nil {
		return dto.SupplierInvoiceStats{}, err
	}
	association, err := s.store.ScopedAssociation(ctx, identity.ScopeUser.ID, paymentCategoryAssociationScope(identity))
	if err != nil {
		return dto.SupplierInvoiceStats{}, err
	}
	rows, err := s.store.SupplierInvoices(ctx, association)
	if err != nil {
		return dto.SupplierInvoiceStats{}, err
	}
	limit := DjangoNaiveNow(s.now(), s.location).Add(30 * 24 * time.Hour)
	stats := dto.SupplierInvoiceStats{Total: dto.NewPythonInt(), Paid: dto.NewPythonInt(), Expiring: []dto.SupplierInvoiceExpiring{}}
	for _, row := range rows {
		var ok bool
		if stats.Total, ok = stats.Total.AddDecimal(row.Amount); !ok {
			return dto.SupplierInvoiceStats{}, errSupplierInvoiceAmount
		}
		if row.Paid {
			stats.Paid, _ = stats.Paid.AddDecimal(row.Amount)
		}
		if row.Paid || row.ExpireDate == nil || row.ExpireDate.After(limit) {
			continue
		}
		amount, _ := dto.DecimalFloat(row.Amount)
		expire := dto.DRFUTCDateTime(*row.ExpireDate)
		stats.Expiring = append(stats.Expiring, dto.SupplierInvoiceExpiring{ExpireDate: &expire, Amount: amount,
			Identifier: row.Identifier, SupplierName: row.SupplierName})
	}
	stats.Unpaid = stats.Total.Sub(stats.Paid)
	return stats, nil
}
