package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type SupplierInvoiceStats struct{ db *sqlx.DB }

func NewSupplierInvoiceStats(db *sqlx.DB) *SupplierInvoiceStats { return &SupplierInvoiceStats{db: db} }

var _ domain.SupplierInvoiceStatsStore = (*SupplierInvoiceStats)(nil)

func (r *SupplierInvoiceStats) ScopedAssociation(ctx context.Context, user string, scope *string) (string, error) {
	var ids []string
	err := r.db.SelectContext(ctx, &ids, `SELECT sport_association_id FROM application_sportassociation
 WHERE user_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR sport_association_id=$2::uuid) LIMIT 2`, user, scope)
	if err != nil {
		return "", err
	}
	if len(ids) != 1 {
		// DoesNotExist or MultipleObjectsReturned.
		return "", errors.Join(domain.ErrAssociationNotFound, sql.ErrNoRows)
	}
	return ids[0], nil
}

func (r *SupplierInvoiceStats) SupplierInvoices(ctx context.Context, association string) ([]domain.SupplierInvoiceStatRow, error) {
	rows := []domain.SupplierInvoiceStatRow{}
	err := r.db.SelectContext(ctx, &rows, `SELECT i.amount::text AS amount, i.paid, i.expire_date, i.invoice_identifier,
 s.name AS supplier_name FROM application_invoicesuppliers i
 LEFT JOIN application_supplierandcustomers s ON s.supplier_id=i.supplier_id
 WHERE i.sport_association_id=$1 ORDER BY i.expire_date DESC`, association)
	return rows, err
}

var _ domain.CustomerInvoiceStatsStore = (*SupplierInvoiceStats)(nil)

func (r *SupplierInvoiceStats) CustomerInvoices(ctx context.Context, association string) ([]domain.CustomerInvoiceStatRow, error) {
	rows := []domain.CustomerInvoiceStatRow{}
	err := r.db.SelectContext(ctx, &rows, `SELECT payment_total_amount::text AS payment_total_amount, paid, payment_expiry_date,
 prefix, number, fiscal_year FROM application_customerinvoice WHERE sport_association_id=$1 ORDER BY payment_expiry_date DESC`, association)
	return rows, err
}
