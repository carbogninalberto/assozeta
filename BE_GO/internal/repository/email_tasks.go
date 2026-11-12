package repository

import (
	"context"
	"database/sql"
	"errors"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// EmailTasks serves the e-mail periodic tasks: partial registrations,
// certificate notices and event reminders.
type EmailTasks struct{ db *sqlx.DB }

func NewEmailTasks(db *sqlx.DB) *EmailTasks { return &EmailTasks{db: db} }

var _ domain.EmailTaskStore = (*EmailTasks)(nil)

func (r *EmailTasks) BeginEmailTask(ctx context.Context, job *domain.Job) (domain.EmailTaskTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	if job != nil {
		err = fenceJobEffects(ctx, tx.tx, job.ID, job.LeaseToken)
	} else {
		var generation int64
		err = tx.tx.GetContext(ctx, &generation, goJobOwnershipCTE+`SELECT generation FROM ownership`)
		if errors.Is(err, sql.ErrNoRows) {
			err = domain.ErrJobLeaseLost
		}
	}
	if err != nil {
		_ = tx.Rollback()
		return nil, err
	}
	return &emailTaskTransaction{ownedTransaction: tx}, nil
}

type emailTaskTransaction struct{ *ownedTransaction }

func (t *emailTaskTransaction) InstanceConfig(ctx context.Context) (domain.EmailRenderInstanceConfig, error) {
	var config domain.EmailRenderInstanceConfig
	err := t.tx.GetContext(ctx, &config, `SELECT true AS found,self_hosted,primary_color,name,logo_path,updated_at
 FROM instance_instanceconfiguration ORDER BY id LIMIT 1`)
	if errors.Is(err, sql.ErrNoRows) {
		return domain.EmailRenderInstanceConfig{}, nil
	}
	return config, err
}

// PartialRegistrations is UserPartial.objects.filter(creation_date__gte=...)
// without ordering.
func (t *emailTaskTransaction) PartialRegistrations(ctx context.Context, since time.Time) ([]domain.UserPartialRegistration, error) {
	var rows []struct {
		ID      int64     `db:"id"`
		Email   string    `db:"email"`
		Created time.Time `db:"creation_date"`
	}
	err := t.tx.SelectContext(ctx, &rows, `SELECT id,email,creation_date FROM bakney_user_partial WHERE creation_date>=$1`, since)
	partials := make([]domain.UserPartialRegistration, len(rows))
	for i, row := range rows {
		partials[i] = domain.UserPartialRegistration{ID: row.ID, Email: row.Email, CreationDate: row.Created}
	}
	return partials, err
}

// DeletePartialRegistration is user_partial.delete(): auditlog's delete entry
// (no association resolves for it), then the row.
func (t *emailTaskTransaction) DeletePartialRegistration(ctx context.Context, row domain.UserPartialRegistration, at time.Time) error {
	key := strconv.FormatInt(row.ID, 10)
	changes, _, err := deleteModelWithAudit(ctx, t.tx, "bakney_user_partial", "id", key)
	if err != nil {
		return err
	}
	id := row.ID
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "userpartial", Key: key, ObjectID: &id,
		Representation: "UserPartial object (" + key + ")", Action: 2, Changes: changes, At: at})
}

// CertificateSubscriptions is the certificate tasks' queryset: live,
// not archived subscriptions created since the cutoff whose certificate
// expires on one of the dates, with the select_related joins.
func (t *emailTaskTransaction) CertificateSubscriptions(ctx context.Context, since time.Time, expirations []time.Time) ([]domain.CertificateSubscription, error) {
	conditions := make([]string, len(expirations))
	arguments := []any{since}
	for i, date := range expirations {
		arguments = append(arguments, date.Format(time.DateOnly))
		conditions[i] = `m.expiration_date=$` + strconv.Itoa(i+2) + `::date`
	}
	where := conditions[0]
	if len(conditions) > 1 {
		where = "(" + strings.Join(conditions, " OR ") + ")"
	}
	var rows []struct {
		ID            uuid.UUID  `db:"subscription_id"`
		Start         *time.Time `db:"start_date"`
		End           *time.Time `db:"end_date"`
		Association   uuid.UUID  `db:"sport_association_id"`
		Denomination  *string    `db:"denomination"`
		OwnerEmail    string     `db:"owner_email"`
		Notifications bool       `db:"medical_certificate_notifications"`
		Associate     *uuid.UUID `db:"associate_id"`
		First         *string    `db:"first_name"`
		Last          *string    `db:"last_name"`
		AssociateMail *string    `db:"associate_email"`
		UserEmail     string     `db:"user_email"`
		Expiration    time.Time  `db:"expiration_date"`
	}
	err := t.tx.SelectContext(ctx, &rows, `SELECT s.subscription_id,s.start_date,s.end_date,a.sport_association_id,a.denomination,
 u.email AS owner_email,u.medical_certificate_notifications,p.associate_id,p.first_name,p.last_name,p.email AS associate_email,
 t6.email AS user_email,m.expiration_date
 FROM application_subscription s INNER JOIN application_medicalcertificate m ON (s.medical_id=m.medical_id)
 INNER JOIN application_sportassociation a ON (s.sport_association_id=a.sport_association_id)
 INNER JOIN bakney_user u ON (a.user_id=u.user_id)
 LEFT OUTER JOIN application_associate p ON (s.associate_id=p.associate_id)
 INNER JOIN bakney_user t6 ON (s.user_id=t6.user_id)
 WHERE (NOT s.archived AND s.creation_date>=$1 AND NOT s.deleted AND `+where+`)`, arguments...)
	subscriptions := make([]domain.CertificateSubscription, len(rows))
	for i, row := range rows {
		subscriptions[i] = domain.CertificateSubscription{SubscriptionID: row.ID, StartDate: row.Start, EndDate: row.End,
			AssociationID: row.Association, Denomination: row.Denomination, OwnerEmail: row.OwnerEmail,
			OwnerNotifications: row.Notifications, AssociateID: row.Associate, AssociateFirstName: row.First,
			AssociateLastName: row.Last, AssociateEmail: row.AssociateMail, UserEmail: row.UserEmail,
			CertificateExpiration: row.Expiration}
	}
	return subscriptions, err
}

// FirstTutorRelation is associate.tutor_relations[.filter(is_primary=True)].first().
func (t *emailTaskTransaction) FirstTutorRelation(ctx context.Context, associate uuid.UUID, primaryOnly bool) (*domain.TutorRelation, error) {
	var row domain.TutorRelation
	query := `SELECT id,associate_id,tutor_id,is_primary FROM application_associatetutorrelation WHERE associate_id=$1`
	if primaryOnly {
		query += ` AND is_primary`
	}
	err := t.tx.QueryRowxContext(ctx, query+` ORDER BY id ASC LIMIT 1`, associate).Scan(&row.ID, &row.Associate, &row.Tutor, &row.Primary)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// MarkTutorRelationPrimary is get_main_tutor's tutor_relation.save() with
// is_primary=True, indexed through the associate's association.
func (t *emailTaskTransaction) MarkTutorRelationPrimary(ctx context.Context, relation domain.TutorRelation, at time.Time) error {
	var association *string
	if err := t.tx.GetContext(ctx, &association, `SELECT sport_association_id::text FROM application_associate WHERE associate_id=$1`,
		relation.Associate); err != nil && !errors.Is(err, sql.ErrNoRows) {
		return err
	}
	key := strconv.FormatInt(relation.ID, 10)
	id := relation.ID
	entry := modelAuditEntry{Model: "associatetutorrelation", Key: key, ObjectID: &id,
		Representation: "AssociateTutorRelation object (" + key + ")", Action: 1,
		Changes: map[string][2]string{"is_primary": {"False", "True"}}, At: at}
	if association != nil {
		path := "associate.sport_association"
		entry.AssociationID, entry.ResolutionPath = association, &path
	}
	if err := appendModelAudit(ctx, t.tx, entry); err != nil {
		return err
	}
	_, err := t.tx.ExecContext(ctx, `UPDATE application_associatetutorrelation SET associate_id=$2,tutor_id=$3,is_primary=true WHERE id=$1`,
		relation.ID, relation.Associate, relation.Tutor)
	return err
}

// AssociateEmail follows tutor_relation.tutor (base manager).
func (t *emailTaskTransaction) AssociateEmail(ctx context.Context, associate uuid.UUID) (*string, error) {
	var email *string
	err := t.tx.GetContext(ctx, &email, `SELECT email FROM application_associate WHERE associate_id=$1`, associate)
	return email, err
}

type communicationConfigurationRow struct {
	ID          uuid.UUID  `db:"communication_configuration_id"`
	Association *uuid.UUID `db:"sport_association_id"`
	Host        *string    `db:"email_smtp_host"`
	Port        *int64     `db:"email_smtp_port"`
	User        *string    `db:"email_smtp_user"`
	Password    *string    `db:"email_smtp_password"`
	SenderName  *string    `db:"email_sender_name"`
	Encryption  string     `db:"email_encryption"`
	Limit       int64      `db:"daily_email_limit"`
	Balance     int64      `db:"daily_email_balance"`
}

const emailTaskConfigurationColumns = `communication_configuration_id,sport_association_id,email_smtp_host,email_smtp_port,
 email_smtp_user,email_smtp_password,email_sender_name,email_encryption,daily_email_limit,daily_email_balance`

func (row communicationConfigurationRow) configuration() domain.CommunicationConfiguration {
	return domain.CommunicationConfiguration{ID: row.ID, AssociationID: row.Association, Host: row.Host, Port: row.Port, User: row.User,
		Password: row.Password, SenderName: row.SenderName, Encryption: row.Encryption, DailyLimit: row.Limit, DailyBalance: row.Balance}
}

// FirstCommunicationConfiguration is CommunicationConfiguration.objects.filter(
// sport_association=...).first().
func (t *emailTaskTransaction) FirstCommunicationConfiguration(ctx context.Context, association uuid.UUID) (*domain.CommunicationConfiguration, error) {
	var row communicationConfigurationRow
	err := t.tx.GetContext(ctx, &row, `SELECT `+emailTaskConfigurationColumns+` FROM communications_communicationconfiguration
 WHERE sport_association_id=$1 ORDER BY communication_configuration_id ASC LIMIT 1`, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	configuration := row.configuration()
	return &configuration, nil
}

// AssociationDenomination follows configuration.sport_association (base manager).
func (t *emailTaskTransaction) AssociationDenomination(ctx context.Context, association uuid.UUID) (*string, error) {
	var denomination *string
	err := t.tx.GetContext(ctx, &denomination, `SELECT denomination FROM application_sportassociation WHERE sport_association_id=$1`, association)
	return denomination, err
}

// SaveCommunicationConfiguration is configuration.save() of the in-memory
// instance: auditlog compares every field but the password with the stored
// row, then every column is written.
func (t *emailTaskTransaction) SaveCommunicationConfiguration(ctx context.Context, configuration domain.CommunicationConfiguration, at time.Time) error {
	var stored communicationConfigurationRow
	err := t.tx.GetContext(ctx, &stored, `SELECT `+emailTaskConfigurationColumns+` FROM communications_communicationconfiguration
 WHERE communication_configuration_id=$1 FOR UPDATE`, configuration.ID)
	exists := err == nil
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return err
	}
	if exists {
		changes := map[string][2]string{}
		add := func(name, old, new string) {
			if old != new {
				changes[name] = [2]string{old, new}
			}
		}
		add("sport_association", optionalUUIDText(stored.Association), optionalUUIDText(configuration.AssociationID))
		add("email_smtp_host", optionalText(stored.Host), optionalText(configuration.Host))
		add("email_smtp_port", optionalIntText(stored.Port), optionalIntText(configuration.Port))
		add("email_smtp_user", optionalText(stored.User), optionalText(configuration.User))
		add("email_sender_name", optionalText(stored.SenderName), optionalText(configuration.SenderName))
		add("email_encryption", stored.Encryption, configuration.Encryption)
		add("daily_email_limit", strconv.FormatInt(stored.Limit, 10), strconv.FormatInt(configuration.DailyLimit, 10))
		add("daily_email_balance", strconv.FormatInt(stored.Balance, 10), strconv.FormatInt(configuration.DailyBalance, 10))
		if len(changes) != 0 {
			id := configuration.ID.String()
			entry := modelAuditEntry{Model: "communicationconfiguration", Key: id,
				Representation: "CommunicationConfiguration object (" + id + ")", Action: 1, Changes: changes, At: at}
			if configuration.AssociationID != nil {
				association, path := configuration.AssociationID.String(), "direct"
				entry.AssociationID, entry.ResolutionPath = &association, &path
			}
			if err = appendModelAuditForApp(ctx, t.tx, "communications", entry); err != nil {
				return err
			}
		}
	}
	// Model.save() updates the row, or inserts it when the UPDATE matched none.
	result, err := t.tx.ExecContext(ctx, `UPDATE communications_communicationconfiguration SET sport_association_id=$2,email_smtp_host=$3,
 email_smtp_port=$4,email_smtp_user=$5,email_smtp_password=$6,email_sender_name=$7,email_encryption=$8,daily_email_limit=$9,
 daily_email_balance=$10 WHERE communication_configuration_id=$1`, configuration.ID, configuration.AssociationID, configuration.Host,
		configuration.Port, configuration.User, configuration.Password, configuration.SenderName, configuration.Encryption,
		configuration.DailyLimit, configuration.DailyBalance)
	if err != nil {
		return err
	}
	if affected, err := result.RowsAffected(); err != nil || affected != 1 {
		if err == nil {
			err = domain.ErrEmailTaskUnsupported
		}
		return err
	}
	return nil
}

// DueReminders is the send_reminders queryset with its select_related joins.
func (t *emailTaskTransaction) DueReminders(ctx context.Context, now time.Time) ([]domain.EventReminder, error) {
	var rows []struct {
		ID           uuid.UUID  `db:"reminders_id"`
		Title        string     `db:"event_title"`
		Description  *string    `db:"event_description"`
		Text         *string    `db:"event_reminder_text"`
		Association  uuid.UUID  `db:"sport_association_id"`
		Denomination *string    `db:"denomination"`
		OwnerEmail   string     `db:"owner_email"`
		UserEmail    string     `db:"user_email"`
		Instructor   *uuid.UUID `db:"instructor_id"`
		Email        *string    `db:"instructor_email"`
	}
	err := t.tx.SelectContext(ctx, &rows, `SELECT r.reminders_id,r.event_title,r.event_description,r.event_reminder_text,
 r.sport_association_id,a.denomination,t5.email AS owner_email,u.email AS user_email,i.instructor_id,i.email AS instructor_email
 FROM application_reminders r INNER JOIN application_sportassociation a ON (r.sport_association_id=a.sport_association_id)
 INNER JOIN bakney_user u ON (r.user_id=u.user_id)
 LEFT OUTER JOIN application_instructor i ON (r.instructor_id=i.instructor_id)
 INNER JOIN bakney_user t5 ON (a.user_id=t5.user_id)
 WHERE (NOT r.completed AND r.send_at<=$1 AND r.sport_association_id IS NOT NULL)`, now)
	reminders := make([]domain.EventReminder, len(rows))
	for i, row := range rows {
		reminders[i] = domain.EventReminder{ID: row.ID, Title: row.Title, Description: row.Description, ReminderText: row.Text,
			AssociationID: row.Association, Denomination: row.Denomination, OwnerEmail: row.OwnerEmail, UserEmail: row.UserEmail,
			HasInstructor: row.Instructor != nil, InstructorEmail: row.Email}
	}
	return reminders, err
}

// CompleteReminder is reminder.completed = True; reminder.save(), audited and
// indexed through its association.
func (t *emailTaskTransaction) CompleteReminder(ctx context.Context, reminder domain.EventReminder, at time.Time) error {
	var completed bool
	err := t.tx.GetContext(ctx, &completed, `SELECT completed FROM application_reminders WHERE reminders_id=$1 FOR UPDATE`, reminder.ID)
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return err
	}
	if err == nil && !completed {
		id, association, path := reminder.ID.String(), reminder.AssociationID.String(), "direct"
		if err = appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "reminders", Key: id, Representation: "Reminders object (" + id + ")",
			Action: 1, Changes: map[string][2]string{"completed": {"False", "True"}}, AssociationID: &association, ResolutionPath: &path,
			At: at}); err != nil {
			return err
		}
	}
	_, err = t.tx.ExecContext(ctx, `UPDATE application_reminders SET completed=true WHERE reminders_id=$1`, reminder.ID)
	return err
}

func (t *emailTaskTransaction) CompleteJob(ctx context.Context, id, token uuid.UUID) error {
	return completeJob(ctx, t.tx, id, token, nil)
}
func (t *emailTaskTransaction) Commit() error   { return t.ownedTransaction.Commit() }
func (t *emailTaskTransaction) Rollback() error { return t.ownedTransaction.Rollback() }

func optionalUUIDText(value *uuid.UUID) string {
	if value == nil {
		return "None"
	}
	return value.String()
}

func optionalIntText(value *int64) string {
	if value == nil {
		return "None"
	}
	return strconv.FormatInt(*value, 10)
}
