package domain

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ErrCommunicationConfigurationNotFound is the views' {"msg": "info not found."}.
var ErrCommunicationConfigurationNotFound = errors.New("info not found.")

// ErrSMTPVerifyNotPorted refuses a verification that would open an SMTP
// connection (smtplib connect, login and the test email): HTTP 500, no write.
var ErrSMTPVerifyNotPorted = refusal.New("SMTP verification needs an outbound connection and is not ported")

// ErrCommunicationNotAssociationRole mirrors User.is_sport_association()
// raising ValueError for a role outside association and collaborator.
var ErrCommunicationNotAssociationRole = errors.New("User is not a sport association")

// CommunicationConfigurationRecord is one communications_communicationconfiguration row.
type CommunicationConfigurationRecord struct {
	ID            string  `db:"communication_configuration_id"`
	AssociationID *string `db:"sport_association_id"`
	Host          *string `db:"email_smtp_host"`
	Port          *int64  `db:"email_smtp_port"`
	User          *string `db:"email_smtp_user"`
	Password      *string `db:"email_smtp_password"`
	SenderName    *string `db:"email_sender_name"`
	Encryption    string  `db:"email_encryption"`
	DailyLimit    int64   `db:"daily_email_limit"`
	DailyBalance  int64   `db:"daily_email_balance"`
}

type CommunicationConfigurationAudit struct {
	ID, AssociationID string
	Action            int
	Changes           map[string][2]string
	Context           LoginAuditContext
	At                time.Time
}

type CommunicationSMTPStore interface {
	BeginCommunicationSMTP(context.Context) (CommunicationSMTPTransaction, error)
}

type CommunicationSMTPTransaction interface {
	// Association resolves SportAssociation.objects.get(user=owner) (active
	// associations only; ErrAssociationNotFound otherwise), optionally locking it.
	Association(ctx context.Context, owner string, lock bool) (string, error)
	// FirstConfiguration is filter(sport_association=...).first(): the lowest key.
	FirstConfiguration(ctx context.Context, association string, lock bool) (CommunicationConfigurationRecord, error)
	InsertConfiguration(context.Context, CommunicationConfigurationRecord) error
	UpdateConfiguration(context.Context, CommunicationConfigurationRecord) error
	AppendConfigurationAudit(context.Context, CommunicationConfigurationAudit) error
	Commit() error
	Rollback() error
}
