package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type CommunicationSMTP struct{ db *sqlx.DB }

func NewCommunicationSMTP(db *sqlx.DB) *CommunicationSMTP { return &CommunicationSMTP{db: db} }

var _ domain.CommunicationSMTPStore = (*CommunicationSMTP)(nil)

func (r *CommunicationSMTP) BeginCommunicationSMTP(ctx context.Context) (domain.CommunicationSMTPTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &communicationSMTPTransaction{ownedTransaction: tx}, nil
}

type communicationSMTPTransaction struct{ *ownedTransaction }

var _ domain.CommunicationSMTPTransaction = (*communicationSMTPTransaction)(nil)

const communicationConfigurationColumns = `SELECT communication_configuration_id,sport_association_id,email_smtp_host,email_smtp_port,
email_smtp_user,email_smtp_password,email_sender_name,email_encryption,daily_email_limit,daily_email_balance
FROM communications_communicationconfiguration`

func (t *communicationSMTPTransaction) Association(ctx context.Context, owner string, lock bool) (string, error) {
	query := `SELECT sport_association_id FROM application_sportassociation WHERE user_id=$1 AND NOT deleted`
	if lock {
		// Serializes info's create-if-missing for one association.
		query += ` FOR UPDATE`
	}
	var association string
	err := t.tx.GetContext(ctx, &association, query, owner)
	if errors.Is(err, sql.ErrNoRows) {
		return "", domain.ErrAssociationNotFound
	}
	return association, err
}

// OwnerEmail is sport_association.user.email (the related user row as stored).
func (t *communicationSMTPTransaction) OwnerEmail(ctx context.Context, association string) (string, error) {
	var email string
	err := t.tx.GetContext(ctx, &email, `SELECT u.email FROM application_sportassociation a
 JOIN bakney_user u ON u.user_id=a.user_id WHERE a.sport_association_id=$1`, association)
	return email, err
}

func (t *communicationSMTPTransaction) FirstConfiguration(ctx context.Context, association string, lock bool) (domain.CommunicationConfigurationRecord, error) {
	query := communicationConfigurationColumns + ` WHERE sport_association_id=$1 ORDER BY communication_configuration_id LIMIT 1`
	if lock {
		query += ` FOR UPDATE`
	}
	var row domain.CommunicationConfigurationRecord
	err := t.tx.GetContext(ctx, &row, query, association)
	if errors.Is(err, sql.ErrNoRows) {
		err = domain.ErrCommunicationConfigurationNotFound
	}
	return row, err
}

func (t *communicationSMTPTransaction) InsertConfiguration(ctx context.Context, row domain.CommunicationConfigurationRecord) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO communications_communicationconfiguration
(communication_configuration_id,sport_association_id,email_smtp_host,email_smtp_port,email_smtp_user,email_smtp_password,
email_sender_name,email_encryption,daily_email_limit,daily_email_balance) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`,
		row.ID, row.AssociationID, row.Host, row.Port, row.User, row.Password, row.SenderName, row.Encryption, row.DailyLimit, row.DailyBalance)
	return err
}

// UpdateConfiguration is Model.save(): every column of the row.
func (t *communicationSMTPTransaction) UpdateConfiguration(ctx context.Context, row domain.CommunicationConfigurationRecord) error {
	result, err := t.tx.ExecContext(ctx, `UPDATE communications_communicationconfiguration SET sport_association_id=$2,
email_smtp_host=$3,email_smtp_port=$4,email_smtp_user=$5,email_smtp_password=$6,email_sender_name=$7,email_encryption=$8,
daily_email_limit=$9,daily_email_balance=$10 WHERE communication_configuration_id=$1`,
		row.ID, row.AssociationID, row.Host, row.Port, row.User, row.Password, row.SenderName, row.Encryption, row.DailyLimit, row.DailyBalance)
	if err != nil {
		return err
	}
	if count, err := result.RowsAffected(); err != nil || count != 1 {
		return errors.Join(err, domain.ErrCommunicationConfigurationNotFound)
	}
	return nil
}

func (t *communicationSMTPTransaction) AppendConfigurationAudit(ctx context.Context, event domain.CommunicationConfigurationAudit) error {
	// The registered direct resolver indexes the row's association.
	path := "direct"
	return appendModelAuditForApp(ctx, t.tx, "communications", modelAuditEntry{Model: "communicationconfiguration", Key: event.ID,
		Representation: "CommunicationConfiguration object (" + event.ID + ")", Action: event.Action, Changes: event.Changes,
		Context: event.Context, AssociationID: &event.AssociationID, ResolutionPath: &path, At: event.At})
}
