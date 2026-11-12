package dto

import (
	"fmt"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

type MessageRead struct {
	ID          uuid.UUID `json:"message_id"`
	Association uuid.UUID `json:"sport_association"`
	Type        string    `json:"type"`
	Message     string    `json:"message"`
	Subject     string    `json:"subject"`
	CreatedAt   *string   `json:"created_at"`
}

type MessageTransactionRead struct {
	ID            uuid.UUID `json:"message_transaction_id"`
	MessageID     uuid.UUID `json:"message"`
	Recipient     string    `json:"recipient"`
	SentOn        string    `json:"sent_on"`
	DeliveredOn   *string   `json:"delivered_on"`
	OpenedOn      *string   `json:"opened_on"`
	ClickedOn     *string   `json:"clicked_on"`
	BouncedOn     *string   `json:"bounced_on"`
	BouncedReason *string   `json:"bounced_reason"`
	BouncedType   *string   `json:"bounced_type"`
	BouncedStatus *string   `json:"bounced_status"`
}

type MessageReadUUIDError struct{ Detail string }

func (e *MessageReadUUIDError) Error() string { return e.Detail }

func MessageReadFromRecord(record domain.MessageReadRecord) MessageRead {
	subject := "None"
	if record.Subject != nil {
		subject = *record.Subject
	}
	return MessageRead{
		ID: record.ID, Association: record.AssociationID, Type: record.Type,
		Message: messageReadsEscape(record.Message), Subject: messageReadsEscape(subject),
		CreatedAt: messageReadsTime(record.CreatedAt),
	}
}

func MessageTransactionReadFromRecord(record domain.MessageTransactionReadRecord) MessageTransactionRead {
	return MessageTransactionRead{
		ID: record.ID, MessageID: record.MessageID, Recipient: record.Recipient,
		SentOn: *messageReadsTime(&record.SentOn), DeliveredOn: messageReadsTime(record.DeliveredOn),
		OpenedOn: messageReadsTime(record.OpenedOn), ClickedOn: messageReadsTime(record.ClickedOn),
		BouncedOn: messageReadsTime(record.BouncedOn), BouncedReason: record.BouncedReason,
		BouncedType: record.BouncedType, BouncedStatus: record.BouncedStatus,
	}
}

func messageReadsEscape(value string) string {
	return strings.NewReplacer(
		"&", "&amp;",
		"<", "&lt;",
		">", "&gt;",
		`"`, "&quot;",
		"'", "&#x27;",
	).Replace(value)
}

func messageReadsTime(value *time.Time) *string {
	if value == nil {
		return nil
	}
	utc := value.UTC()
	text := utc.Format("2006-01-02T15:04:05")
	if microsecond := utc.Nanosecond() / 1000; microsecond != 0 {
		text += fmt.Sprintf(".%06d", microsecond)
	}
	text += "Z"
	return &text
}
