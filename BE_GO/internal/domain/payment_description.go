package domain

import "errors"

// ErrPaymentDescription prevents saving when the current model hook cannot
// resolve a description (invalid historical JSON or an incomplete relation).
var ErrPaymentDescription = errors.New("invalid payment description")

// PaymentDescriptionRelation is the first row by primary key for ONE relation
// kind, including soft-deleted course subscriptions/installments. The caller
// must obtain this from an authorized, consistent, bounded database read.
type PaymentDescriptionRelation struct {
	Title         *string
	Meta          any // Stored related payment metadata, independently of pending changes.
	MissingCarnet bool
}

// PaymentDescriptionInput contains resolved inputs, not client-supplied IDs.
// JSON uses RequestObject (ordered fields), []any, json.Number, string, bool or
// nil. Related slots are course subscription, installment and carnet, in that
// order. Category selection and resource authorization are separate obligations.
type PaymentDescriptionInput struct {
	Subject                   int
	Description, CategoryName *string
	Meta, Course              any
	Related                   [3]*PaymentDescriptionRelation
}
