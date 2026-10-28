package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

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
	rows := []domain.BalanceAccount{}
	err := r.db.SelectContext(ctx, &rows, balanceAccountsQuery(ordered, false), association, group)
	return rows, err
}

// balanceAccountsQuery is CustomAccountSerializer's balance and deletable for
// the association's accounts ($3 restricts it to one account).
func balanceAccountsQuery(ordered, one bool) string {
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
	if one {
		query += ` AND a.custom_account_id=$3`
	}
	if ordered {
		query += ` ORDER BY a.editable`
	}
	return query
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

var _ domain.BalanceSheetStore = (*BalanceAccounts)(nil)

func (r *BalanceAccounts) OpenSheets(ctx context.Context, association string) ([]domain.BalanceSheetRow, error) {
	rows := []domain.BalanceSheetRow{}
	err := r.db.SelectContext(ctx, &rows, `SELECT data::text AS data, creation_date, year, status_flag FROM application_balancesheet
 WHERE sport_association_id=$1 AND NOT archived ORDER BY creation_date DESC`, association)
	return rows, err
}

type balanceAccountWriteTransaction struct{ *paymentCategoryTransaction }

func (r *BalanceAccounts) BeginBalanceAccountWrite(ctx context.Context) (domain.BalanceAccountWriteTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &balanceAccountWriteTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}, nil
}

func (t *balanceAccountWriteTransaction) OwnerAssociation(ctx context.Context, owner string) (string, error) {
	return paymentCategoryAssociation(ctx, t.tx, owner, nil, false)
}

func (t *balanceAccountWriteTransaction) AccountIDTaken(ctx context.Context, id string, exclude *string) (bool, error) {
	var taken bool
	err := t.tx.GetContext(ctx, &taken, `SELECT EXISTS(SELECT 1 FROM application_customaccounts
 WHERE custom_account_id=$1 AND ($2::uuid IS NULL OR custom_account_id<>$2::uuid))`, id, exclude)
	return taken, err
}

const balanceAccountColumns = `custom_account_id, enabled, name, initial_balance::text AS initial_balance, account_type, account_code,
 sport_association_id, editable`

func (t *balanceAccountWriteTransaction) AccountForUpdate(ctx context.Context, id, association string) (*domain.BalanceAccountRow, error) {
	var row domain.BalanceAccountRow
	err := t.tx.GetContext(ctx, &row, `SELECT `+balanceAccountColumns+` FROM application_customaccounts
 WHERE custom_account_id=$1 AND sport_association_id=$2 FOR UPDATE`, id, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func balanceAccountAudit(id string, action int, changes map[string][2]string, association string, audit domain.LoginAuditContext, at time.Time) modelAuditEntry {
	path := "direct"
	return modelAuditEntry{Model: "customaccounts", Key: id, Representation: "CustomAccounts object (" + id + ")", Action: action,
		Changes: changes, Context: audit, At: at, AssociationID: &association, ResolutionPath: &path}
}

func (t *balanceAccountWriteTransaction) InsertAccount(ctx context.Context, row domain.BalanceAccountRow, audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_customaccounts
 (custom_account_id,enabled,name,initial_balance,account_type,account_code,sport_association_id,editable)
 VALUES ($1,$2,$3,$4::numeric,$5,$6,$7,$8)`, row.ID, row.Enabled, row.Name, row.InitialBalance, row.AccountType, row.AccountCode,
		row.AssociationID, row.Editable); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_customaccounts", "custom_account_id", row.ID)
	if err != nil {
		return err
	}
	// The transfer foreign keys declare related_names, rendered as managers.
	manager := [2]string{"None", "application.CustomAccountsTransfer.None"}
	changes["custom_account_from"], changes["custom_account_to"] = manager, manager
	return appendModelAudit(ctx, t.tx, balanceAccountAudit(row.ID, 0, changes, row.AssociationID, audit, at))
}

func (t *balanceAccountWriteTransaction) SaveAccount(ctx context.Context, row domain.BalanceAccountRow, changes map[string][2]string,
	audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_customaccounts SET enabled=$2, name=$3, initial_balance=$4::numeric,
 account_type=$5, account_code=$6, editable=$7 WHERE custom_account_id=$1`, row.ID, row.Enabled, row.Name, row.InitialBalance,
		row.AccountType, row.AccountCode, row.Editable); err != nil {
		return err
	}
	if len(changes) == 0 {
		return nil
	}
	return appendModelAudit(ctx, t.tx, balanceAccountAudit(row.ID, 1, changes, row.AssociationID, audit, at))
}

func (t *balanceAccountWriteTransaction) SerializedAccount(ctx context.Context, association, id string, group *string) (domain.BalanceAccount, error) {
	var row domain.BalanceAccount
	err := t.tx.GetContext(ctx, &row, balanceAccountsQuery(false, true), association, group, id)
	return row, err
}
