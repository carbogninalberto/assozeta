package domain

import (
	"context"
	"time"
)

// BalanceAccount is one CustomAccounts row with its exact current balance
// (numeric text) and the facts deletable needs.
type BalanceAccount struct {
	ID             string `db:"custom_account_id"`
	Enabled        bool   `db:"enabled"`
	Name           string `db:"name"`
	InitialBalance string `db:"initial_balance"`
	AccountType    int    `db:"account_type"`
	AccountCode    string `db:"account_code"`
	AssociationID  string `db:"sport_association_id"`
	Editable       bool   `db:"editable"`
	CurrentBalance string `db:"current_balance"`
	Moved          bool   `db:"moved"`
	HasPayments    bool   `db:"has_payments"`
}

type BalanceTransferAccount struct {
	ID          *string `db:"id"`
	Name        *string `db:"name"`
	AccountType *int    `db:"account_type"`
	AccountCode *string `db:"account_code"`
}

type BalanceTransfer struct {
	ID            string                 `db:"custom_account_transfer_id"`
	Amount        string                 `db:"amount"`
	Date          time.Time              `db:"date"`
	AssociationID *string                `db:"sport_association_id"`
	From          BalanceTransferAccount `db:"from_account"`
	To            BalanceTransferAccount `db:"to_account"`
}

type BalanceAccountStore interface {
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	// Accounts computes balances through Payment.objects (live, optionally
	// group-filtered payments); ordered by editable when ordered is set.
	Accounts(ctx context.Context, association string, group *string, ordered bool) ([]BalanceAccount, error)
	Transfers(ctx context.Context, association string) ([]BalanceTransfer, error)
}

// BalanceSheetRow is one non-archived balance sheet; Data is the jsonb text.
type BalanceSheetRow struct {
	Data         *string   `db:"data"`
	CreationDate time.Time `db:"creation_date"`
	Year         int       `db:"year"`
	StatusFlag   int       `db:"status_flag"`
}

type BalanceSheetStore interface {
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	// OpenSheets lists archived=False sheets, newest first.
	OpenSheets(ctx context.Context, association string) ([]BalanceSheetRow, error)
}

type BalanceAccountRow struct {
	ID             string `db:"custom_account_id"`
	Enabled        bool   `db:"enabled"`
	Name           string `db:"name"`
	InitialBalance string `db:"initial_balance"`
	AccountType    int    `db:"account_type"`
	AccountCode    string `db:"account_code"`
	AssociationID  string `db:"sport_association_id"`
	Editable       bool   `db:"editable"`
}

type BalanceAccountWriteTransaction interface {
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	AccountIDTaken(ctx context.Context, id string, exclude *string) (bool, error)
	// AccountForUpdate locks CustomAccounts.get(id, association); nil when absent.
	AccountForUpdate(ctx context.Context, id, association string) (*BalanceAccountRow, error)
	InsertAccount(ctx context.Context, row BalanceAccountRow, audit LoginAuditContext, at time.Time) error
	SaveAccount(ctx context.Context, row BalanceAccountRow, changes map[string][2]string, audit LoginAuditContext, at time.Time) error
	// SerializedAccount is the account with its balance and deletable.
	SerializedAccount(ctx context.Context, association, id string, group *string) (BalanceAccount, error)
	Commit() error
	Rollback() error
}

type BalanceAccountWriteStore interface {
	BeginBalanceAccountWrite(context.Context) (BalanceAccountWriteTransaction, error)
}

type BalanceTransferRow struct {
	ID            string    `db:"custom_account_transfer_id"`
	From          *string   `db:"custom_account_from_id"`
	To            *string   `db:"custom_account_to_id"`
	Amount        string    `db:"amount"`
	Date          time.Time `db:"date"`
	AssociationID *string   `db:"sport_association_id"`
}

type BalanceTransferWriteTransaction interface {
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	// AccountsOf reports which of ids are accounts of the association.
	AccountsOf(ctx context.Context, association string, ids []string) (map[string]bool, error)
	TransferIDTaken(ctx context.Context, id string, exclude *string) (bool, error)
	TransferForUpdate(ctx context.Context, id, association string) (*BalanceTransferRow, error)
	InsertTransfer(ctx context.Context, row BalanceTransferRow, audit LoginAuditContext, at time.Time) error
	SaveTransfer(ctx context.Context, row BalanceTransferRow, changes map[string][2]string, audit LoginAuditContext, at time.Time) error
	DeleteTransfer(ctx context.Context, id string, audit LoginAuditContext, at time.Time) error
	Commit() error
	Rollback() error
}

type BalanceTransferWriteStore interface {
	BeginBalanceTransferWrite(context.Context) (BalanceTransferWriteTransaction, error)
}
