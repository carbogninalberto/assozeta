package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// CommunicationSendEmail stores configuration_send_email's writes; every
// Django create and save autocommits, so callers use one transaction per step.
type CommunicationSendEmail struct{ db *sqlx.DB }

func NewCommunicationSendEmail(db *sqlx.DB) *CommunicationSendEmail {
	return &CommunicationSendEmail{db: db}
}

var _ domain.CommunicationSendEmailStore = (*CommunicationSendEmail)(nil)

func (r *CommunicationSendEmail) BeginCommunicationSendEmail(ctx context.Context) (domain.CommunicationSendEmailTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &communicationSendEmailTransaction{&communicationSMTPTransaction{ownedTransaction: tx}}, nil
}

type communicationSendEmailTransaction struct{ *communicationSMTPTransaction }

var _ domain.CommunicationSendEmailTransaction = (*communicationSendEmailTransaction)(nil)

func (t *communicationSendEmailTransaction) StoredMessage(ctx context.Context, id, association string) (*domain.SendEmailMessage, error) {
	var row domain.SendEmailMessage
	err := t.tx.GetContext(ctx, &row, `SELECT message_id,sport_association_id,message,subject FROM communications_message
WHERE message_id=$1 AND sport_association_id=$2 ORDER BY message_id LIMIT 1`, id, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &row, err
}

func (t *communicationSendEmailTransaction) InsertMessage(ctx context.Context, message domain.SendEmailMessage, at time.Time,
	audit domain.LoginAuditContext) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO communications_message(message_id,sport_association_id,type,message,subject,created_at)
VALUES($1,$2,'EMAIL',$3,$4,$5)`, message.ID, message.AssociationID, message.Message, message.Subject, at); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "communications_message", "message_id", message.ID)
	if err != nil {
		return err
	}
	path := "direct"
	return appendModelAuditForApp(ctx, t.tx, "communications", modelAuditEntry{Model: "message", Key: message.ID,
		Representation: "Message object (" + message.ID + ")", Action: 0, Changes: changes, Context: audit,
		AssociationID: &message.AssociationID, ResolutionPath: &path, At: at})
}

func (t *communicationSendEmailTransaction) InsertMessageTransaction(ctx context.Context, id, message, recipient string, at time.Time) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO communications_messagetransaction(message_transaction_id,message_id,recipient,sent_on)
VALUES($1,$2,$3,$4)`, id, message, recipient, at)
	return err
}

func (t *communicationSendEmailTransaction) ConfigurationByID(ctx context.Context, id string) (domain.CommunicationConfigurationRecord, error) {
	var row domain.CommunicationConfigurationRecord
	err := t.tx.GetContext(ctx, &row, communicationConfigurationColumns+` WHERE communication_configuration_id=$1 FOR UPDATE`, id)
	if errors.Is(err, sql.ErrNoRows) {
		err = domain.ErrCommunicationConfigurationNotFound
	}
	return row, err
}

func (t *communicationSendEmailTransaction) Denomination(ctx context.Context, association string) (*string, error) {
	var denomination *string
	err := t.tx.GetContext(ctx, &denomination, `SELECT denomination FROM application_sportassociation WHERE sport_association_id=$1`, association)
	return denomination, err
}
