package repository

import (
	"context"
	"database/sql"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type InvoiceDeletes struct{ db *sqlx.DB }

func NewInvoiceDeletes(db *sqlx.DB) *InvoiceDeletes { return &InvoiceDeletes{db: db} }

var _ domain.InvoiceDeleteStore = (*InvoiceDeletes)(nil)

func (r *InvoiceDeletes) BeginInvoiceDelete(ctx context.Context) (domain.InvoiceDeleteTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &invoiceDeleteTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}, nil
}

type invoiceDeleteTransaction struct{ *paymentCategoryTransaction }

func (t *invoiceDeleteTransaction) DeleteInvoice(ctx context.Context, kind domain.InvoiceDeleteKind, id, association string, audit domain.LoginAuditContext, at time.Time) error {
	var owned bool
	if err := t.tx.GetContext(ctx, &owned, `SELECT EXISTS(SELECT 1 FROM `+kind.Table+` WHERE `+kind.Key+`=$1 AND sport_association_id=$2)`,
		id, association); err != nil {
		return err
	}
	if !owned {
		return domain.ErrInvoiceDeleteMissing
	}
	changes, _, err := deleteModelWithAudit(ctx, t.tx, kind.Table, kind.Key, id)
	if err != nil {
		return err
	}
	// The row is gone, so resolution falls back to the changes JSON.
	path := "changes_json.sport_association"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: kind.Model, Key: id, Representation: kind.Representation + " object (" + id + ")",
		Action: 2, Changes: changes, Context: audit, AssociationID: &association, ResolutionPath: &path, At: at})
}
