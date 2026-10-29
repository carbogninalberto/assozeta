package dto

import (
	"encoding/json"
	"strconv"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// PythonDecimalFloat is DRF's JSONEncoder for a Decimal: float(value), in
// Python's repr.
func PythonDecimalFloat(text string) (json.Number, error) {
	value, err := strconv.ParseFloat(text, 64)
	if err != nil {
		return "", err
	}
	return json.Number(pythonFloatRepr(value)), nil
}

// BalanceAccountItem is CustomAccountSerializer.
type BalanceAccountItem struct {
	ID             string      `json:"custom_account_id"`
	Enabled        bool        `json:"enabled"`
	Name           string      `json:"name"`
	CurrentBalance json.Number `json:"current_balance"`
	InitialBalance string      `json:"initial_balance"`
	AccountType    int         `json:"account_type"`
	AccountCode    string      `json:"account_code"`
	Deletable      bool        `json:"deletable"`
	Editable       bool        `json:"editable"`
}

// BalanceAccountRaw is the view's related != 'true' dictionary.
type BalanceAccountRaw struct {
	ID             string      `json:"custom_account_id"`
	Enabled        bool        `json:"enabled"`
	Name           string      `json:"name"`
	InitialBalance json.Number `json:"initial_balance"`
	AccountType    int         `json:"account_type"`
	AccountCode    string      `json:"account_code"`
	AssociationID  string      `json:"sport_association_id"`
	Editable       bool        `json:"editable"`
}

type BalanceTransferAccountItem struct {
	ID          string `json:"custom_account_id"`
	Name        string `json:"name"`
	AccountType int    `json:"account_type"`
	AccountCode string `json:"account_code"`
}

// BalanceTransferItem is CustomAccountTransferInfoSerializer.
type BalanceTransferItem struct {
	ID            string                      `json:"custom_account_transfer_id"`
	From          *BalanceTransferAccountItem `json:"custom_account_from"`
	To            *BalanceTransferAccountItem `json:"custom_account_to"`
	Amount        string                      `json:"amount"`
	Date          string                      `json:"date"`
	AssociationID *string                     `json:"sport_association"`
}

func BalanceAccountItemFrom(row domain.BalanceAccount) (BalanceAccountItem, error) {
	balance, err := PythonDecimalFloat(row.CurrentBalance)
	return BalanceAccountItem{ID: row.ID, Enabled: row.Enabled, Name: row.Name, CurrentBalance: balance, InitialBalance: row.InitialBalance,
		AccountType: row.AccountType, AccountCode: row.AccountCode, Deletable: row.Editable && !row.Moved && !row.HasPayments,
		Editable: row.Editable}, err
}

func BalanceAccountRawFrom(row domain.BalanceAccount) (BalanceAccountRaw, error) {
	initial, err := PythonDecimalFloat(row.InitialBalance)
	return BalanceAccountRaw{ID: row.ID, Enabled: row.Enabled, Name: row.Name, InitialBalance: initial, AccountType: row.AccountType,
		AccountCode: row.AccountCode, AssociationID: row.AssociationID, Editable: row.Editable}, err
}

func balanceTransferAccount(row domain.BalanceTransferAccount) *BalanceTransferAccountItem {
	if row.ID == nil {
		return nil
	}
	return &BalanceTransferAccountItem{ID: *row.ID, Name: *row.Name, AccountType: *row.AccountType, AccountCode: *row.AccountCode}
}

func BalanceTransferItemFrom(row domain.BalanceTransfer) BalanceTransferItem {
	return BalanceTransferItem{ID: row.ID, From: balanceTransferAccount(row.From), To: balanceTransferAccount(row.To), Amount: row.Amount,
		Date: DRFUTCDateTime(row.Date), AssociationID: row.AssociationID}
}

// BalanceSheetItem is one entry of balance_sheet_archived.
type BalanceSheetItem struct {
	Data         json.RawMessage `json:"data"`
	CreationDate string          `json:"creation_date"`
	Year         int             `json:"year"`
	Draft        bool            `json:"draft"`
}

func BalanceSheetItemFrom(row domain.BalanceSheetRow) BalanceSheetItem {
	return BalanceSheetItem{Data: rawJSON(row.Data), CreationDate: DRFUTCDateTime(row.CreationDate), Year: row.Year, Draft: row.StatusFlag == 1}
}
