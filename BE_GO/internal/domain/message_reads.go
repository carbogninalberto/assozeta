package domain

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"
)

var ErrMessageReadForbidden = errors.New("message read forbidden")
var ErrMessageReadNotFound = errors.New("message read not found")

// MessageReadRecord is the list serializer projection. Subject and CreatedAt
// retain database nullability; HTML escaping belongs to the response DTO.
type MessageReadRecord struct {
	ID            uuid.UUID  `db:"message_id"`
	AssociationID uuid.UUID  `db:"sport_association_id"`
	Type          string     `db:"type"`
	Message       string     `db:"message"`
	Subject       *string    `db:"subject"`
	CreatedAt     *time.Time `db:"created_at"`
}

// MessageTransactionReadRecord includes every delivery-history field exposed by
// Django's MessageTransactionSerializer.
type MessageTransactionReadRecord struct {
	ID            uuid.UUID  `db:"message_transaction_id"`
	MessageID     uuid.UUID  `db:"message_id"`
	Recipient     string     `db:"recipient"`
	SentOn        time.Time  `db:"sent_on"`
	DeliveredOn   *time.Time `db:"delivered_on"`
	OpenedOn      *time.Time `db:"opened_on"`
	ClickedOn     *time.Time `db:"clicked_on"`
	BouncedOn     *time.Time `db:"bounced_on"`
	BouncedReason *string    `db:"bounced_reason"`
	BouncedType   *string    `db:"bounced_type"`
	BouncedStatus *string    `db:"bounced_status"`
}

type MessageReadStore interface {
	ListMessages(context.Context, string) ([]MessageReadRecord, error)
	MessageTransactions(context.Context, string, string) ([]MessageTransactionReadRecord, error)
}
