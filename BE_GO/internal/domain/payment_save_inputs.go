package domain

import (
	"context"
	"encoding/json"
	"errors"

	"github.com/google/uuid"
)

var ErrPaymentSaveBatch = errors.New("invalid payment save input batch")
var ErrPaymentSaveScope = errors.New("unavailable payment save reference")

const PaymentSaveBatchLimit = 512

// PaymentSaveReference carries trusted background-workflow ownership. It is not
// an HTTP actor/group permission grant. UUID zero is a valid persisted identity.
type PaymentSaveReference struct{ PaymentID, AssociationID uuid.UUID }
type PaymentSaveInvoice struct {
	ID       uuid.UUID
	Archived bool
}
type PaymentSaveRelation struct {
	Title         *string
	MissingCarnet bool
}
type PaymentSaveState struct {
	ID, AssociationID              uuid.UUID
	Archived                       bool
	Subject                        int
	OriginalCategoryID, CategoryID *uuid.UUID
	Description, CategoryName      *string
	Meta, Course                   json.RawMessage
	Invoice                        *PaymentSaveInvoice
	Related                        [3]*PaymentSaveRelation
}
type PaymentSavePlan struct {
	Payment     PaymentSaveState
	Description *string
}
type PaymentSaveInputReader interface {
	ReadPaymentSaveInputs(context.Context, []PaymentSaveReference, string) ([]PaymentSaveState, error)
}

// UniquePaymentSaveReferences bounds raw requests before deduplication and keeps
// first occurrence order. One payment cannot be assigned two association scopes.
func UniquePaymentSaveReferences(refs []PaymentSaveReference) ([]PaymentSaveReference, error) {
	if len(refs) > PaymentSaveBatchLimit {
		return nil, ErrPaymentSaveBatch
	}
	seen := make(map[uuid.UUID]uuid.UUID, len(refs))
	var unique []PaymentSaveReference
	for _, ref := range refs {
		if association, exists := seen[ref.PaymentID]; exists {
			if association != ref.AssociationID {
				return nil, ErrPaymentSaveBatch
			}
			continue
		}
		seen[ref.PaymentID] = ref.AssociationID
		unique = append(unique, ref)
	}
	return unique, nil
}
