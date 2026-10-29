package service

import (
	"context"
	"errors"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type BalanceAccountWrites struct {
	store    domain.BalanceAccountWriteStore
	accounts *BalanceAccounts
	now      func() time.Time
	ids      func() string
}

func NewBalanceAccountWrites(store domain.BalanceAccountWriteStore, accounts *BalanceAccounts, now func() time.Time, ids func() string) *BalanceAccountWrites {
	return &BalanceAccountWrites{store: store, accounts: accounts, now: now, ids: ids}
}

// ErrBalanceAccountMissing is {'exception': 'CustomAccounts matching query does not exist.'}.
var ErrBalanceAccountMissing = errors.New("custom account does not exist")

// ErrBalanceAccountPrimaryKeyChange refuses a new custom_account_id on update:
// Model.save() would insert a copy under the new key (not ported).
var ErrBalanceAccountPrimaryKeyChange = errors.New("custom account primary key change is not ported")

// BalanceAccountValidationError is DRF's 400 field map.
type BalanceAccountValidationError struct{ Fields map[string]any }

func (e *BalanceAccountValidationError) Error() string { return "invalid custom account input" }

func (s *BalanceAccountWrites) write(ctx context.Context, identity domain.ResolvedIdentity, raw *string, body any, form bool, group string,
	audit domain.LoginAuditContext) (dto.BalanceAccountItem, error) {
	var item dto.BalanceAccountItem
	if _, err := s.accounts.association(ctx, identity); err != nil {
		return item, err
	}
	tx, err := s.store.BeginBalanceAccountWrite(ctx)
	if err != nil {
		return item, err
	}
	defer tx.Rollback()
	association, err := tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return item, err
	}
	var row *domain.BalanceAccountRow
	if raw != nil {
		id, err := paymentInfoParseUUID(*raw)
		if err != nil {
			return item, ErrBalanceAccountMissing
		}
		if row, err = tx.AccountForUpdate(ctx, id.String(), association); err != nil {
			return item, err
		}
		if row == nil {
			return item, ErrBalanceAccountMissing
		}
	}
	write, invalid := dto.ParseBalanceAccountWrite(body, row != nil, form)
	if write.ID != nil {
		var exclude *string
		if row != nil {
			exclude = &row.ID
		}
		taken, err := tx.AccountIDTaken(ctx, *write.ID, exclude)
		if err != nil {
			return item, err
		}
		if taken {
			if invalid == nil {
				invalid = map[string]any{}
			}
			invalid["custom_account_id"] = []string{dto.BalanceAccountUnique}
		}
	}
	if invalid != nil {
		return item, &BalanceAccountValidationError{Fields: invalid}
	}
	auditContext, at := paymentCategoryAuditContext(identity, audit), s.now().UTC().Truncate(time.Microsecond)
	if row == nil {
		created := domain.BalanceAccountRow{ID: s.ids(), Enabled: true, AccountType: 1, Editable: true, AssociationID: association}
		if write.ID != nil {
			created.ID = *write.ID
		}
		applyBalanceAccountWrite(&created, write)
		if err = tx.InsertAccount(ctx, created, auditContext, at); err != nil {
			return item, err
		}
		row = &created
	} else {
		if write.ID != nil && *write.ID != row.ID {
			return item, ErrBalanceAccountPrimaryKeyChange
		}
		old := *row
		applyBalanceAccountWrite(row, write)
		changes := map[string][2]string{}
		diff := func(name, before, after string) {
			if before != after {
				changes[name] = [2]string{before, after}
			}
		}
		diff("enabled", pythonBool(old.Enabled), pythonBool(row.Enabled))
		diff("name", old.Name, row.Name)
		diff("initial_balance", old.InitialBalance, row.InitialBalance)
		diff("account_type", strconv.Itoa(old.AccountType), strconv.Itoa(row.AccountType))
		diff("account_code", old.AccountCode, row.AccountCode)
		diff("editable", pythonBool(old.Editable), pythonBool(row.Editable))
		if err = tx.SaveAccount(ctx, *row, changes, auditContext, at); err != nil {
			return item, err
		}
	}
	serialized, err := tx.SerializedAccount(ctx, association, row.ID, supplierGroup(group))
	if err != nil {
		return item, err
	}
	if item, err = dto.BalanceAccountItemFrom(serialized); err != nil {
		return item, err
	}
	return item, tx.Commit()
}

func applyBalanceAccountWrite(row *domain.BalanceAccountRow, write dto.BalanceAccountWrite) {
	if write.Enabled != nil {
		row.Enabled = *write.Enabled
	}
	if write.Editable != nil {
		row.Editable = *write.Editable
	}
	if write.Name != nil {
		row.Name = *write.Name
	}
	if write.AccountCode != nil {
		row.AccountCode = *write.AccountCode
	}
	if write.InitialBalance != nil {
		row.InitialBalance = *write.InitialBalance
	}
	if write.AccountType != nil {
		row.AccountType = *write.AccountType
	}
}

// Add follows balance_sheet_accounts_add.
func (s *BalanceAccountWrites) Add(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool, group string,
	audit domain.LoginAuditContext) (dto.BalanceAccountItem, error) {
	return s.write(ctx, identity, nil, body, form, group, audit)
}

// Update follows balance_sheet_accounts_update.
func (s *BalanceAccountWrites) Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, form bool, group string,
	audit domain.LoginAuditContext) (dto.BalanceAccountItem, error) {
	return s.write(ctx, identity, &raw, body, form, group, audit)
}

type BalanceTransferWrites struct {
	store    domain.BalanceTransferWriteStore
	accounts *BalanceAccounts
	now      func() time.Time
	ids      func() string
}

func NewBalanceTransferWrites(store domain.BalanceTransferWriteStore, accounts *BalanceAccounts, now func() time.Time, ids func() string) *BalanceTransferWrites {
	return &BalanceTransferWrites{store: store, accounts: accounts, now: now, ids: ids}
}

// ErrBalanceTransferMissing is {'exception': 'CustomAccountsTransfer matching query does not exist.'}.
var ErrBalanceTransferMissing = errors.New("custom account transfer does not exist")

func balanceTransferItem(row domain.BalanceTransferRow) map[string]any {
	return map[string]any{"custom_account_transfer_id": row.ID, "custom_account_from": row.From, "custom_account_to": row.To,
		"amount": row.Amount, "date": dto.DRFUTCDateTime(row.Date), "sport_association": row.AssociationID}
}

func balanceAuditMoment(value time.Time) string {
	value = value.UTC()
	if value.Nanosecond()/1000 == 0 {
		return value.Format("2006-01-02 15:04:05")
	}
	return value.Format("2006-01-02 15:04:05.000000")
}

func (s *BalanceTransferWrites) begin(ctx context.Context, identity domain.ResolvedIdentity, raw *string) (domain.BalanceTransferWriteTransaction,
	string, *domain.BalanceTransferRow, error) {
	if _, err := s.accounts.association(ctx, identity); err != nil {
		return nil, "", nil, err
	}
	tx, err := s.store.BeginBalanceTransferWrite(ctx)
	if err != nil {
		return nil, "", nil, err
	}
	association, err := tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		tx.Rollback()
		return nil, "", nil, err
	}
	if raw == nil {
		return tx, association, nil, nil
	}
	id, err := paymentInfoParseUUID(*raw)
	if err != nil {
		tx.Rollback()
		return nil, "", nil, ErrBalanceTransferMissing
	}
	row, err := tx.TransferForUpdate(ctx, id.String(), association)
	if err == nil && row == nil {
		err = ErrBalanceTransferMissing
	}
	if err != nil {
		tx.Rollback()
		return nil, "", nil, err
	}
	return tx, association, row, nil
}

// write follows balance_sheet_accounts_transfer_add / _update.
func (s *BalanceTransferWrites) write(ctx context.Context, identity domain.ResolvedIdentity, raw *string, body any,
	audit domain.LoginAuditContext) (map[string]any, error) {
	tx, association, row, err := s.begin(ctx, identity, raw)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	write, invalid := dto.ParseBalanceTransferWrite(body, row != nil)
	if invalid == nil {
		invalid = map[string]any{}
	}
	if _, nonField := invalid["non_field_errors"]; !nonField {
		candidates := []string{}
		for _, id := range []*string{write.From, write.To} {
			if id != nil {
				candidates = append(candidates, *id)
			}
		}
		owned, err := tx.AccountsOf(ctx, association, candidates)
		if err != nil {
			return nil, err
		}
		if write.From != nil && !owned[*write.From] {
			invalid["custom_account_from"] = []string{"related-does-not-exist:" + write.FromRaw}
		}
		if write.To != nil && !owned[*write.To] {
			invalid["custom_account_to"] = []string{"related-does-not-exist:" + write.ToRaw}
		}
		if write.ID != nil {
			var exclude *string
			if row != nil {
				exclude = &row.ID
			}
			taken, err := tx.TransferIDTaken(ctx, *write.ID, exclude)
			if err != nil {
				return nil, err
			}
			if taken {
				invalid["custom_account_transfer_id"] = []string{dto.BalanceTransferUnique}
			}
		}
	}
	if len(invalid) > 0 {
		return nil, &BalanceAccountValidationError{Fields: invalid}
	}
	auditContext, at := paymentCategoryAuditContext(identity, audit), s.now().UTC().Truncate(time.Microsecond)
	if row == nil {
		created := domain.BalanceTransferRow{ID: s.ids(), From: write.From, To: write.To, Amount: *write.Amount, Date: at, AssociationID: &association}
		if write.ID != nil {
			created.ID = *write.ID
		}
		if write.Date != nil {
			created.Date = *write.Date
		}
		if err = tx.InsertTransfer(ctx, created, auditContext, at); err != nil {
			return nil, err
		}
		return balanceTransferItem(created), tx.Commit()
	}
	if write.ID != nil && *write.ID != row.ID {
		return nil, ErrBalanceAccountPrimaryKeyChange
	}
	old := *row
	if write.FromPresent {
		row.From = write.From
	}
	if write.ToPresent {
		row.To = write.To
	}
	if write.Amount != nil {
		row.Amount = *write.Amount
	}
	if write.Date != nil {
		row.Date = *write.Date
	}
	row.AssociationID = &association
	changes := map[string][2]string{}
	diff := func(name, before, after string) {
		if before != after {
			changes[name] = [2]string{before, after}
		}
	}
	diff("custom_account_from", supplierValue(old.From), supplierValue(row.From))
	diff("custom_account_to", supplierValue(old.To), supplierValue(row.To))
	diff("amount", old.Amount, row.Amount)
	diff("date", balanceAuditMoment(old.Date), balanceAuditMoment(row.Date))
	diff("sport_association", supplierValue(old.AssociationID), supplierValue(row.AssociationID))
	if err = tx.SaveTransfer(ctx, *row, changes, auditContext, at); err != nil {
		return nil, err
	}
	return balanceTransferItem(*row), tx.Commit()
}

func (s *BalanceTransferWrites) Add(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) (map[string]any, error) {
	return s.write(ctx, identity, nil, body, audit)
}

func (s *BalanceTransferWrites) Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any,
	audit domain.LoginAuditContext) (map[string]any, error) {
	return s.write(ctx, identity, &raw, body, audit)
}

// Delete follows balance_sheet_accounts_transfer_delete.
func (s *BalanceTransferWrites) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error {
	tx, _, row, err := s.begin(ctx, identity, &raw)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = tx.DeleteTransfer(ctx, row.ID, paymentCategoryAuditContext(identity, audit), s.now().UTC().Truncate(time.Microsecond)); err != nil {
		return err
	}
	return tx.Commit()
}
