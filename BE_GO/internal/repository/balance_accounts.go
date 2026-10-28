package repository

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type BalanceAccounts struct{ db *sqlx.DB }

func NewBalanceAccounts(db *sqlx.DB) *BalanceAccounts { return &BalanceAccounts{db: db} }

var _ domain.BalanceAccountStore = (*BalanceAccounts)(nil)

func (r *BalanceAccounts) OwnerAssociation(ctx context.Context, owner string) (string, error) {
	return paymentCategoryAssociation(ctx, r.db, owner, nil, false)
}

// One query: CustomAccountSerializer's balance and deletable per account. FK
// filters on transfers ignore managers; payments use the soft-delete,
// group-aware manager.
func (r *BalanceAccounts) Accounts(ctx context.Context, association string, group *string, ordered bool) ([]domain.BalanceAccount, error) {
	query := `WITH payments AS (SELECT custom_accounts_id, paid, expense, amount FROM application_payment
  WHERE NOT deleted AND ($2::uuid IS NULL OR group_id=$2::uuid))
 SELECT a.custom_account_id, a.enabled, a.name, a.initial_balance::text AS initial_balance, a.account_type, a.account_code,
  a.sport_association_id, a.editable,
  (a.initial_balance
   + COALESCE((SELECT sum(t.amount) FROM application_customaccountstransfer t WHERE t.custom_account_to_id=a.custom_account_id), 0)
   - COALESCE((SELECT sum(t.amount) FROM application_customaccountstransfer t WHERE t.custom_account_from_id=a.custom_account_id), 0)
   + COALESCE((SELECT sum(CASE WHEN p.expense THEN -p.amount ELSE p.amount END) FROM payments p
     WHERE p.custom_accounts_id=a.custom_account_id AND p.paid), 0))::text AS current_balance,
  EXISTS(SELECT 1 FROM application_customaccountstransfer t
   WHERE t.custom_account_from_id=a.custom_account_id OR t.custom_account_to_id=a.custom_account_id) AS moved,
  EXISTS(SELECT 1 FROM payments p WHERE p.custom_accounts_id=a.custom_account_id) AS has_payments
 FROM application_customaccounts a WHERE a.sport_association_id=$1`
	if ordered {
		query += ` ORDER BY a.editable`
	}
	rows := []domain.BalanceAccount{}
	err := r.db.SelectContext(ctx, &rows, query, association, group)
	return rows, err
}

func (r *BalanceAccounts) Transfers(ctx context.Context, association string) ([]domain.BalanceTransfer, error) {
	rows := []domain.BalanceTransfer{}
	err := r.db.SelectContext(ctx, &rows, `SELECT t.custom_account_transfer_id, t.amount::text AS amount, t.date, t.sport_association_id,
  f.custom_account_id AS "from_account.id", f.name AS "from_account.name", f.account_type AS "from_account.account_type",
  f.account_code AS "from_account.account_code",
  o.custom_account_id AS "to_account.id", o.name AS "to_account.name", o.account_type AS "to_account.account_type",
  o.account_code AS "to_account.account_code"
 FROM application_customaccountstransfer t
 LEFT JOIN application_customaccounts f ON f.custom_account_id=t.custom_account_from_id
 LEFT JOIN application_customaccounts o ON o.custom_account_id=t.custom_account_to_id
 WHERE t.sport_association_id=$1`, association)
	return rows, err
}
