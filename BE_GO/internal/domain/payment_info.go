package domain

import (
	"context"
	"encoding/json"
	"time"

	"github.com/google/uuid"
)

// PaymentInfoLookup carries already resolved request scope. AssociationID is
// only the association-bound impersonation scope, never an ordinary caller's
// owner. Nil GroupID preserves the characterized absent/malformed-group lookup.
// UUID-wide public lookup remains an unapproved disclosure-policy baseline.
type PaymentInfoLookup struct {
	PaymentID              uuid.UUID
	AssociationID, GroupID *uuid.UUID
}

type PaymentInfoReader interface {
	// A missing or filtered payment returns nil, nil.
	ReadPaymentInfo(context.Context, PaymentInfoLookup) (*PaymentInfoRecord, error)
	ReadPaymentInfoCategoryNames(context.Context, []uuid.UUID) (map[uuid.UUID]string, error)
}

// PaymentInfoRecord is a read projection, not the public response. The service
// owns subject handling, relationship precedence and metadata enrichment.
// Monetary values and stored JSON never pass through floating point here.
type PaymentInfoRecord struct {
	ID                          uuid.UUID
	Subject                     int
	Amount                      *string
	CreatedAt                   *time.Time
	User                        *PaymentInfoUser
	Associate                   *PaymentInfoAssociate
	Association                 *PaymentInfoAssociation
	Course, Meta                json.RawMessage
	CategoryName                *string
	Direct, Installment, Carnet *PaymentInfoRelation
}

type PaymentInfoUser struct {
	FirstName, LastName, Email, Phone, Username *string
	Role                                        int
}

type PaymentInfoAssociate struct {
	FirstName, LastName, TaxCode *string
}

type PaymentInfoAssociation struct {
	ID                                  uuid.UUID
	OwnerID                             *uuid.UUID
	Denomination, TaxCode, CheckoutInfo *string
}

type PaymentInfoRelation struct {
	ID       uuid.UUID
	Offering *PaymentInfoOffering
}

type PaymentInfoOffering struct {
	ID                      uuid.UUID
	Title, Description, Fee *string
	Association             *PaymentInfoAssociation
}
