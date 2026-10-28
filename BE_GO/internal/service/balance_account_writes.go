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
