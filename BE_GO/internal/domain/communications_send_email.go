package domain

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ErrSendEmailMessageNotFound is configuration_send_email's 404
// {'msg': 'message not found.'} for a message_id outside the association.
var ErrSendEmailMessageNotFound = errors.New("message not found.")

// ErrSendEmailServerError is an exception Django does not handle in
// configuration_send_email (a non-dict body, a non-string email, a
// message_id UUIDField rejects, an immutable QueryDict, a missing
// configuration, an exception returned by send_email): the 500 page.
var ErrSendEmailServerError = errors.New("configuration_send_email raised")

// ErrSendEmailNotPorted refuses, before any write, an SMTP delivery whose
// message or envelope is outside smtpsend's subset.
var ErrSendEmailNotPorted = refusal.New("SMTP message outside the ported subset")

// SendEmailMessage is one communications_message row.
type SendEmailMessage struct {
	ID            string  `db:"message_id"`
	AssociationID string  `db:"sport_association_id"`
	Message       string  `db:"message"`
	Subject       *string `db:"subject"`
}

type CommunicationSendEmailStore interface {
	BeginCommunicationSendEmail(context.Context) (CommunicationSendEmailTransaction, error)
}

type CommunicationSendEmailTransaction interface {
	// Association is SportAssociation.objects.get(user=owner) (ErrAssociationNotFound otherwise).
	Association(ctx context.Context, owner string, lock bool) (string, error)
	// StoredMessage is Message.objects.filter(message_id=..., sport_association=...).first().
	StoredMessage(ctx context.Context, id, association string) (*SendEmailMessage, error)
	// InsertMessage creates an EMAIL message with its auditlog create entry (index direct).
	InsertMessage(ctx context.Context, message SendEmailMessage, at time.Time, audit LoginAuditContext) error
	InsertMessageTransaction(ctx context.Context, id, message, recipient string, at time.Time) error
	// FirstConfiguration is filter(sport_association=...).first(); ErrCommunicationConfigurationNotFound without one.
	FirstConfiguration(ctx context.Context, association string, lock bool) (CommunicationConfigurationRecord, error)
	// ConfigurationByID locks the stored row for the save's audit diff.
	ConfigurationByID(ctx context.Context, id string) (CommunicationConfigurationRecord, error)
	UpdateConfiguration(context.Context, CommunicationConfigurationRecord) error
	AppendConfigurationAudit(context.Context, CommunicationConfigurationAudit) error
	Denomination(ctx context.Context, association string) (*string, error)
	Commit() error
	Rollback() error
}
