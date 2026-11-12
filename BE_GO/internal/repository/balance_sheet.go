package repository

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

var _ domain.BalanceSheetWriteStore = (*BalanceAccounts)(nil)
var _ domain.BalanceAccountDeleteStore = (*BalanceAccounts)(nil)

type balanceSheetTransaction struct{ *paymentCategoryTransaction }

func (r *BalanceAccounts) BeginBalanceSheetWrite(ctx context.Context) (domain.BalanceSheetWriteTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &balanceSheetTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}, nil
}

func (t *balanceSheetTransaction) SheetAssociation(ctx context.Context, owner string) (string, error) {
	var id string
	err := t.tx.GetContext(ctx, &id, `SELECT sport_association_id FROM application_sportassociation WHERE user_id=$1`, owner)
	if errors.Is(err, sql.ErrNoRows) {
		return "", domain.ErrAssociationNotFound
	}
	return id, err
}

func (t *balanceSheetTransaction) StartDate(ctx context.Context, user string) (int64, int64, error) {
	var row struct {
		Day   int64 `db:"balance_sheet_start_day"`
		Month int64 `db:"balance_sheet_start_month"`
	}
	err := t.tx.GetContext(ctx, &row, `SELECT balance_sheet_start_day, balance_sheet_start_month FROM bakney_user WHERE user_id=$1`, user)
	return row.Day, row.Month, err
}

const balanceSheetColumns = `balance_sheet_id, sport_association_id, status_flag, creation_date, year, data::text AS data, archived`

func (t *balanceSheetTransaction) sheet(ctx context.Context, query string, args ...any) (*domain.BalanceSheetRecord, error) {
	var row domain.BalanceSheetRecord
	err := t.tx.GetContext(ctx, &row, `SELECT `+balanceSheetColumns+` FROM application_balancesheet WHERE `+query+
		` ORDER BY creation_date DESC LIMIT 1 FOR UPDATE`, args...)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *balanceSheetTransaction) SheetOfYear(ctx context.Context, association string, year int) (*domain.BalanceSheetRecord, error) {
	return t.sheet(ctx, `year=$1 AND sport_association_id=$2`, year, association)
}

func (t *balanceSheetTransaction) NewestSheet(ctx context.Context, association string) (*domain.BalanceSheetRecord, error) {
	return t.sheet(ctx, `sport_association_id=$1`, association)
}

// SheetPayments mirrors generate_balance_sheet's queryset (the soft-delete,
// group-aware manager and select_related('payment_category')), unordered.
func (t *balanceSheetTransaction) SheetPayments(ctx context.Context, association string, from, to time.Time, group *string) ([]domain.BalanceSheetPayment, error) {
	rows := []domain.BalanceSheetPayment{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT p.amount::text AS amount, p.expense, p.meta_payment_categories::text AS meta,
  c.payment_category_id AS category_id, c.name AS category_name, c.type AS category_type
 FROM application_payment p LEFT OUTER JOIN application_paymentcategory c ON (p.payment_category_id = c.payment_category_id)
 WHERE NOT p.deleted AND ($4::uuid IS NULL OR p.group_id=$4::uuid) AND p.sport_association_id=$1 AND p.paid AND p.amount > 0
  AND NOT p.deleted AND NOT p.archived
  AND ((p.payment_date BETWEEN $2 AND $3 AND p.payment_date IS NOT NULL) OR (p.creation_date BETWEEN $2 AND $3 AND p.payment_date IS NULL))`,
		association, from, to, group)
	return rows, err
}

func (t *balanceSheetTransaction) Category(ctx context.Context, id string) (*domain.BalanceSheetCategory, error) {
	var row domain.BalanceSheetCategory
	err := t.tx.GetContext(ctx, &row, `SELECT payment_category_id, name, type FROM application_paymentcategory
 WHERE payment_category_id=$1 ORDER BY payment_category_id LIMIT 1`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// AccountBalances is CustomAccountSerializer.get_current_balance_from(
// datetime(1970, 1, 1), to) per account: transfers by plain foreign key,
// payments through the soft-delete, group-aware manager.
func (t *balanceSheetTransaction) AccountBalances(ctx context.Context, association string, kind int, to time.Time, group *string) ([]string, error) {
	rows := []string{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT (a.initial_balance
   + COALESCE((SELECT sum(t.amount) FROM application_customaccountstransfer t
     WHERE t.custom_account_to_id=a.custom_account_id AND t.date BETWEEN $3 AND $4), 0)
   - COALESCE((SELECT sum(t.amount) FROM application_customaccountstransfer t
     WHERE t.custom_account_from_id=a.custom_account_id AND t.date BETWEEN $3 AND $4), 0)
   + COALESCE((SELECT sum(CASE WHEN p.expense THEN -p.amount ELSE p.amount END) FROM application_payment p
     WHERE NOT p.deleted AND ($5::uuid IS NULL OR p.group_id=$5::uuid) AND p.custom_accounts_id=a.custom_account_id AND p.paid
      AND p.payment_date BETWEEN $3 AND $4), 0))::text
 FROM application_customaccounts a WHERE a.sport_association_id=$1 AND a.account_type=$2`,
		association, kind, time.Date(1970, 1, 1, 0, 0, 0, 0, time.UTC), to, group)
	return rows, err
}

func balanceSheetAudit(id string, action int, changes map[string][2]string, association *string, audit domain.LoginAuditContext, at time.Time) modelAuditEntry {
	path := "direct"
	return modelAuditEntry{Model: "balancesheet", Key: id, Representation: "BalanceSheet object (" + id + ")", Action: action,
		Changes: changes, Context: audit, At: at, AssociationID: association, ResolutionPath: &path}
}

func (t *balanceSheetTransaction) InsertSheet(ctx context.Context, row domain.BalanceSheetRecord, audit domain.LoginAuditContext, at time.Time) (*string, error) {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_balancesheet
 (balance_sheet_id, sport_association_id, status_flag, creation_date, year, data, archived) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)`,
		row.ID, row.AssociationID, row.StatusFlag, row.CreationDate, row.Year, row.Data, row.Archived); err != nil {
		return nil, err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_balancesheet", "balance_sheet_id", row.ID)
	if err != nil {
		return nil, err
	}
	if err = appendModelAudit(ctx, t.tx, balanceSheetAudit(row.ID, 0, changes, row.AssociationID, audit, at)); err != nil {
		return nil, err
	}
	var stored *string
	err = t.tx.GetContext(ctx, &stored, `SELECT data::text FROM application_balancesheet WHERE balance_sheet_id=$1`, row.ID)
	return stored, err
}

func (t *balanceSheetTransaction) AuditSheetUpdate(ctx context.Context, row domain.BalanceSheetRecord, changes map[string][2]string,
	audit domain.LoginAuditContext, at time.Time) error {
	if len(changes) == 0 {
		return nil
	}
	return appendModelAudit(ctx, t.tx, balanceSheetAudit(row.ID, 1, changes, row.AssociationID, audit, at))
}

func (t *balanceSheetTransaction) SaveSheet(ctx context.Context, row domain.BalanceSheetRecord, changes map[string][2]string,
	audit domain.LoginAuditContext, at time.Time) error {
	if len(changes) > 0 {
		if err := appendModelAudit(ctx, t.tx, balanceSheetAudit(row.ID, 1, changes, row.AssociationID, audit, at)); err != nil {
			return err
		}
	}
	if _, err := t.tx.ExecContext(ctx, `SAVEPOINT balance_sheet_save`); err != nil {
		return err
	}
	_, err := t.tx.ExecContext(ctx, `UPDATE application_balancesheet SET sport_association_id=$2, status_flag=$3, creation_date=$4, year=$5,
 data=$6::jsonb, archived=$7 WHERE balance_sheet_id=$1`, row.ID, row.AssociationID, row.StatusFlag, row.CreationDate, row.Year, row.Data,
		row.Archived)
	if err != nil {
		if _, rollback := t.tx.ExecContext(ctx, `ROLLBACK TO SAVEPOINT balance_sheet_save`); rollback != nil {
			return errors.Join(err, rollback)
		}
		return errors.Join(domain.ErrBalanceSheetSaveFailed, err)
	}
	_, err = t.tx.ExecContext(ctx, `RELEASE SAVEPOINT balance_sheet_save`)
	return err
}

type balanceAccountDeleteTransaction struct {
	*balanceAccountWriteTransaction
}

func (r *BalanceAccounts) BeginBalanceAccountDelete(ctx context.Context) (domain.BalanceAccountDeleteTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &balanceAccountDeleteTransaction{&balanceAccountWriteTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}}, nil
}

// DeleteAccount is CustomAccounts.delete() through the shared collector.
func (t *balanceAccountDeleteTransaction) DeleteAccount(ctx context.Context, id string, audit domain.LoginAuditContext, at time.Time,
	group *string) error {
	const label = "application.CustomAccounts"
	model := collaboratorDeleteGraphLoaded.Models[label]
	if model == nil {
		return fmt.Errorf("%w: %s is not in the model graph", ErrCollaboratorDeleteUnsupported, label)
	}
	run := &collaboratorDeleteRun{tx: t.tx, graph: collaboratorDeleteGraphLoaded, audit: audit, group: group, now: func() time.Time { return at }}
	rows, err := run.load(ctx, model, `t.custom_account_id = $1::uuid`, id)
	if err != nil {
		return err
	}
	return run.Delete(ctx, label, rows)
}
