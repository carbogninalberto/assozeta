package domain

import (
	"context"
	"time"

	"github.com/google/uuid"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

const (
	JobSendUserPartialRegistrationEmail = "send_user_partial_registration_email"
	JobSendExpiringCertificateEmail     = "send_expiring_certificate_email"
	JobSendExpiredCertificateEmail      = "send_expired_certificate_email"
	JobSendReminders                    = "send_reminders"
)

// ErrEmailTaskUnsupported refuses a run, before any effect, whose SMTP
// message or envelope is outside the proven smtpsend subset.
var ErrEmailTaskUnsupported = refusal.New("email task input outside the ported subset")

// UserPartialRegistration is one bakney_user_partial row.
type UserPartialRegistration struct {
	ID           int64
	Email        string
	CreationDate time.Time
}

// CertificateSubscription is one row of the certificate tasks' queryset with
// its select_related associate, association, owner, certificate and user.
type CertificateSubscription struct {
	SubscriptionID        uuid.UUID
	StartDate, EndDate    *time.Time
	AssociationID         uuid.UUID
	Denomination          *string
	OwnerEmail            string
	OwnerNotifications    bool
	AssociateID           *uuid.UUID
	AssociateFirstName    *string
	AssociateLastName     *string
	AssociateEmail        *string
	UserEmail             string
	CertificateExpiration time.Time
}

// TutorRelation is one AssociateTutorRelation row.
type TutorRelation struct {
	ID        int64
	Associate uuid.UUID
	Tutor     uuid.UUID
	Primary   bool
}

// CommunicationConfiguration is the in-memory CommunicationConfiguration
// instance: every column as loaded.
type CommunicationConfiguration struct {
	ID            uuid.UUID
	AssociationID *uuid.UUID
	Host          *string
	Port          *int64
	User          *string
	Password      *string
	SenderName    *string
	Encryption    string
	DailyLimit    int64
	DailyBalance  int64
}

// EventReminder is one Reminders row with its association, owner, user and
// instructor (select_related).
type EventReminder struct {
	ID              uuid.UUID
	Title           string
	Description     *string
	ReminderText    *string
	AssociationID   uuid.UUID
	Denomination    *string
	OwnerEmail      string
	UserEmail       string
	HasInstructor   bool
	InstructorEmail *string
}

type EmailTaskStore interface {
	// BeginEmailTask fences the job; a nil job fences only the Go ownership
	// (work a task left running after its completion, like Django's Timer).
	BeginEmailTask(context.Context, *Job) (EmailTaskTransaction, error)
}

type EmailTaskTransaction interface {
	InstanceConfig(context.Context) (EmailRenderInstanceConfig, error)
	PartialRegistrations(ctx context.Context, since time.Time) ([]UserPartialRegistration, error)
	DeletePartialRegistration(ctx context.Context, row UserPartialRegistration, at time.Time) error
	CertificateSubscriptions(ctx context.Context, since time.Time, expirations []time.Time) ([]CertificateSubscription, error)
	FirstTutorRelation(ctx context.Context, associate uuid.UUID, primaryOnly bool) (*TutorRelation, error)
	MarkTutorRelationPrimary(ctx context.Context, relation TutorRelation, at time.Time) error
	AssociateEmail(ctx context.Context, associate uuid.UUID) (*string, error)
	FirstCommunicationConfiguration(ctx context.Context, association uuid.UUID) (*CommunicationConfiguration, error)
	AssociationDenomination(ctx context.Context, association uuid.UUID) (*string, error)
	SaveCommunicationConfiguration(ctx context.Context, configuration CommunicationConfiguration, at time.Time) error
	DueReminders(ctx context.Context, now time.Time) ([]EventReminder, error)
	CompleteReminder(ctx context.Context, reminder EventReminder, at time.Time) error
	CompleteJob(context.Context, uuid.UUID, uuid.UUID) error
	Commit() error
	Rollback() error
}

// SMTPServer is the configuration's connection settings.
type SMTPServer struct {
	Host, User, Password, Encryption string
	Port                             int
}

// SMTPMessage is the MIMEMultipart CommunicationConfiguration.send_email builds.
type SMTPMessage struct {
	SenderName, SenderUser string
	To                     []string
	Subject                string
	ReplyTo                []string
	HTML                   string
}

// SMTPMailer is verify_smtp(send_email=False) and the sending connection.
type SMTPMailer interface {
	Verify(context.Context, SMTPServer) error
	Send(context.Context, SMTPServer, SMTPMessage) error
	// Check reports ErrEmailTaskUnsupported for a message Send would refuse.
	Check(SMTPServer, SMTPMessage) error
}
