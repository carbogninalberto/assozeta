package dto

import (
	"encoding/json"
	"math/big"
)

// PythonNumber is a running Python sum that starts as the integer 0 and
// becomes a Decimal once an amount is added; DRF renders Decimal as float().
type PythonNumber struct {
	Value   *big.Rat
	Decimal bool
}

func NewPythonInt() PythonNumber { return PythonNumber{Value: new(big.Rat)} }

// AddDecimal parses a PostgreSQL numeric text and adds it.
func (n PythonNumber) AddDecimal(text string) (PythonNumber, bool) {
	value, ok := new(big.Rat).SetString(text)
	if !ok {
		return n, false
	}
	return PythonNumber{Value: new(big.Rat).Add(n.Value, value), Decimal: true}, true
}

// Sub is Python's - : Decimal when either operand is Decimal.
func (n PythonNumber) Sub(other PythonNumber) PythonNumber {
	return PythonNumber{Value: new(big.Rat).Sub(n.Value, other.Value), Decimal: n.Decimal || other.Decimal}
}

func (n PythonNumber) MarshalJSON() ([]byte, error) {
	if !n.Decimal {
		return []byte(n.Value.Num().String()), nil
	}
	value, _ := n.Value.Float64()
	return []byte(pythonFloatRepr(value)), nil
}

// DecimalFloat renders a Decimal value as DRF's JSONEncoder does (float()).
func DecimalFloat(text string) (json.RawMessage, bool) {
	value, ok := new(big.Rat).SetString(text)
	if !ok {
		return nil, false
	}
	number, _ := value.Float64()
	return json.RawMessage(pythonFloatRepr(number)), true
}

type SupplierInvoiceExpiring struct {
	ExpireDate   *string         `json:"expire_date"`
	Amount       json.RawMessage `json:"amount"`
	Identifier   string          `json:"invoice_identifier"`
	SupplierName *string         `json:"supplier__name"`
}

type SupplierInvoiceStats struct {
	Total    PythonNumber              `json:"invoices_total"`
	Paid     PythonNumber              `json:"invoices_paid"`
	Unpaid   PythonNumber              `json:"invoices_unpaid"`
	Expiring []SupplierInvoiceExpiring `json:"invoices_expiring"`
}
