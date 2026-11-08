package service

import (
	"context"
	"errors"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
)

// CommunicationSMTP ports configuration_smtp_info, _update and _verify.
type CommunicationSMTP struct {
	store domain.CommunicationSMTPStore
	now   func() time.Time
}

func NewCommunicationSMTP(store domain.CommunicationSMTPStore, now func() time.Time) *CommunicationSMTP {
	return &CommunicationSMTP{store: store, now: now}
}

// begin resolves request.user.sport_association (emailLogsOwner: athletes are
// denied, collaborators use their connected owner) inside one transaction.
func (s *CommunicationSMTP) begin(ctx context.Context, identity domain.ResolvedIdentity) (domain.CommunicationSMTPTransaction, string, string, error) {
	if err := ctx.Err(); err != nil {
		return nil, "", "", err
	}
	owner, err := emailLogsOwner(identity)
	if err != nil {
		return nil, "", "", err
	}
	tx, err := s.store.BeginCommunicationSMTP(ctx)
	if err != nil {
		return nil, "", "", err
	}
	association, err := tx.Association(ctx, owner, false)
	if err != nil {
		_ = tx.Rollback()
		return nil, "", "", err
	}
	return tx, owner, association, nil
}

func smtpAuditText(value any) string {
	switch typed := value.(type) {
	case *string:
		if typed == nil {
			return "None"
		}
		return *typed
	case *int64:
		if typed == nil {
			return "None"
		}
		return strconv.FormatInt(*typed, 10)
	case int64:
		return strconv.FormatInt(typed, 10)
	case string:
		return typed
	}
	return "None"
}

// smtpAuditFields are the audited columns in model order; auditlog excludes email_smtp_password.
func smtpAuditFields(row domain.CommunicationConfigurationRecord) [][2]any {
	return [][2]any{{"email_smtp_host", row.Host}, {"email_smtp_port", row.Port}, {"email_smtp_user", row.User},
		{"email_sender_name", row.SenderName}, {"email_encryption", row.Encryption}, {"daily_email_limit", row.DailyLimit},
		{"daily_email_balance", row.DailyBalance}}
}

// Info returns the association's first configuration, creating the default
// one (port 587, TLS) when there is none, audited as a create.
func (s *CommunicationSMTP) Info(ctx context.Context, identity domain.ResolvedIdentity, audit domain.LoginAuditContext) (dto.CommunicationConfigurationInfo, error) {
	tx, owner, association, err := s.begin(ctx, identity)
	if err != nil {
		return dto.CommunicationConfigurationInfo{}, err
	}
	defer tx.Rollback()
	row, err := tx.FirstConfiguration(ctx, association, false)
	if errors.Is(err, domain.ErrCommunicationConfigurationNotFound) {
		row, err = s.create(ctx, tx, identity, owner, association, audit)
	}
	if err != nil {
		return dto.CommunicationConfigurationInfo{}, err
	}
	if err = ctx.Err(); err != nil {
		return dto.CommunicationConfigurationInfo{}, err
	}
	return dto.CommunicationConfigurationInfoFrom(row), tx.Commit()
}

func (s *CommunicationSMTP) create(ctx context.Context, tx domain.CommunicationSMTPTransaction, identity domain.ResolvedIdentity,
	owner, association string, audit domain.LoginAuditContext) (domain.CommunicationConfigurationRecord, error) {
	// is_sport_association() raises ValueError (500) for any other role.
	if role := identity.ScopeUser.Role; role != domain.RoleAssociation && role != domain.RoleCollaborator {
		return domain.CommunicationConfigurationRecord{}, domain.ErrCommunicationNotAssociationRole
	}
	if _, err := tx.Association(ctx, owner, true); err != nil {
		return domain.CommunicationConfigurationRecord{}, err
	}
	// Re-read under the association lock so concurrent requests create one row.
	if row, err := tx.FirstConfiguration(ctx, association, false); !errors.Is(err, domain.ErrCommunicationConfigurationNotFound) {
		return row, err
	}
	port := int64(587)
	row := domain.CommunicationConfigurationRecord{ID: uuid.NewString(), AssociationID: &association, Port: &port, Encryption: "TLS",
		DailyLimit: 1000, DailyBalance: 0}
	if err := tx.InsertConfiguration(ctx, row); err != nil {
		return row, err
	}
	changes := map[string][2]string{"communication_configuration_id": {"None", row.ID}, "sport_association": {"None", association}}
	for _, field := range smtpAuditFields(row) {
		// auditlog's create diff omits None-valued non-relation fields.
		if text := smtpAuditText(field[1]); text != "None" {
			changes[field[0].(string)] = [2]string{"None", text}
		}
	}
	err := tx.AppendConfigurationAudit(ctx, domain.CommunicationConfigurationAudit{ID: row.ID, AssociationID: association, Action: 0,
		Changes: changes, Context: paymentCategoryAuditContext(identity, audit), At: s.now().UTC().Truncate(time.Microsecond)})
	return row, err
}

// Update validates the partial patch serializer, saves every column and
// audits the changed fields except the password (no entry without changes).
func (s *CommunicationSMTP) Update(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool, audit domain.LoginAuditContext) error {
	tx, _, association, err := s.begin(ctx, identity)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	old, err := tx.FirstConfiguration(ctx, association, true)
	if err != nil {
		return err
	}
	patch, err := dto.ParseCommunicationSMTPPatch(body, form)
	if err != nil {
		return err
	}
	row := old
	for _, field := range []struct {
		value  **string
		target **string
	}{{patch.Host, &row.Host}, {patch.User, &row.User}, {patch.Password, &row.Password}, {patch.SenderName, &row.SenderName}} {
		if field.value != nil {
			*field.target = *field.value
		}
	}
	if patch.Port != nil {
		row.Port = *patch.Port
	}
	if patch.Encryption != nil {
		row.Encryption = *patch.Encryption
	}
	if err = tx.UpdateConfiguration(ctx, row); err != nil {
		return err
	}
	changes := map[string][2]string{}
	before := smtpAuditFields(old)
	for index, field := range smtpAuditFields(row) {
		if previous, current := smtpAuditText(before[index][1]), smtpAuditText(field[1]); previous != current {
			changes[field[0].(string)] = [2]string{previous, current}
		}
	}
	if len(changes) > 0 {
		if err = tx.AppendConfigurationAudit(ctx, domain.CommunicationConfigurationAudit{ID: row.ID, AssociationID: association, Action: 1,
			Changes: changes, Context: paymentCategoryAuditContext(identity, audit), At: s.now().UTC().Truncate(time.Microsecond)}); err != nil {
			return err
		}
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	return tx.Commit()
}

// Verify answers verify_smtp() for the configurations that fail before any
// network I/O and refuses every other one (domain.ErrSMTPVerifyNotPorted).
func (s *CommunicationSMTP) Verify(ctx context.Context, identity domain.ResolvedIdentity) (bool, string, error) {
	tx, _, association, err := s.begin(ctx, identity)
	if err != nil {
		return false, "", err
	}
	defer tx.Rollback()
	row, err := tx.FirstConfiguration(ctx, association, false)
	if err != nil {
		return false, "", err
	}
	message, err := SMTPVerifyWithoutConnection(row)
	if err != nil {
		return false, "", err
	}
	return false, message, tx.Commit()
}

var pythonASCIIInt = regexp.MustCompile(`^[+-]?[0-9](?:_?[0-9])*$`)

// SMTPVerifyWithoutConnection reproduces smtplib for configurations that
// raise before opening a socket: SMTP()/SMTP_SSL() connect only for a truthy
// host, so login() or starttls() raise SMTPServerDisconnected('please run
// connect() first'); connect() with a falsy port splits a host holding exactly
// one ':' and int() of a non-numeric suffix raises OSError('nonnumeric port').
// Anything else would connect: it is refused.
func SMTPVerifyWithoutConnection(row domain.CommunicationConfigurationRecord) (string, error) {
	if row.Host == nil || *row.Host == "" {
		return "please run connect() first", nil
	}
	host := *row.Host
	if (row.Port == nil || *row.Port == 0) && strings.Count(host, ":") == 1 {
		suffix := host[strings.Index(host, ":")+1:]
		for _, character := range suffix {
			// Python int() accepts non-ASCII digits and whitespace; only ASCII is classified here.
			if character >= 0x80 || character >= 0x1c && character <= 0x1f {
				return "", domain.ErrSMTPVerifyNotPorted
			}
		}
		if !pythonASCIIInt.MatchString(strings.Trim(suffix, " \t\n\r\v\f")) {
			return "nonnumeric port", nil
		}
	}
	return "", domain.ErrSMTPVerifyNotPorted
}
