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
