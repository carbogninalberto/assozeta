package service

import (
	"context"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type CustomerInvoiceStats struct {
	store    domain.CustomerInvoiceStatsStore
	now      func() time.Time
	location *time.Location
}

func NewCustomerInvoiceStats(store domain.CustomerInvoiceStatsStore, now func() time.Time, location *time.Location) *CustomerInvoiceStats {
	return &CustomerInvoiceStats{store: store, now: now, location: location}
}

// Stats follows invoice_customers_stats. The due date is a DateField, so the
// naive datetime bounds compare by their dates: today through today + 30 days.
func (s *CustomerInvoiceStats) Stats(ctx context.Context, identity domain.ResolvedIdentity) (dto.CustomerInvoiceStats, error) {
	if err := ctx.Err(); err != nil {
		return dto.CustomerInvoiceStats{}, err
	}
	association, err := s.store.ScopedAssociation(ctx, identity.ScopeUser.ID, paymentCategoryAssociationScope(identity))
	if err != nil {
		return dto.CustomerInvoiceStats{}, err
	}
	rows, err := s.store.CustomerInvoices(ctx, association)
	if err != nil {
		return dto.CustomerInvoiceStats{}, err
	}
	now := DjangoNaiveNow(s.now(), s.location)
	first := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC)
	late := now.Add(30 * 24 * time.Hour)
	last := time.Date(late.Year(), late.Month(), late.Day(), 0, 0, 0, 0, time.UTC)
	stats := dto.CustomerInvoiceStats{Total: dto.NewPythonInt(), Paid: dto.NewPythonInt(), Expiring: []dto.CustomerInvoiceExpiring{}}
	for _, row := range rows {
		var ok bool
		if stats.Total, ok = stats.Total.AddDecimal(row.Amount); !ok {
			return dto.CustomerInvoiceStats{}, errSupplierInvoiceAmount
		}
		if row.Paid {
			stats.Paid, _ = stats.Paid.AddDecimal(row.Amount)
		}
		due := time.Date(row.ExpiryDate.Year(), row.ExpiryDate.Month(), row.ExpiryDate.Day(), 0, 0, 0, 0, time.UTC)
		if row.Paid || due.Before(first) || due.After(last) {
			continue
		}
		amount, _ := dto.DecimalFloat(row.Amount)
		stats.Expiring = append(stats.Expiring, dto.CustomerInvoiceExpiring{ExpiryDate: due.Format("2006-01-02"), Amount: amount,
			Prefix: row.Prefix, Number: row.Number, FiscalYear: row.FiscalYear})
	}
	stats.Unpaid = stats.Total.Sub(stats.Paid)
	return stats, nil
}
