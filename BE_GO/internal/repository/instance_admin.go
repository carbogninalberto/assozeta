package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// InstanceAdmin reads and saves the singleton InstanceConfiguration for the
// owner-only administration views. It never writes auditlog rows: the model
// is not registered with auditlog.
type InstanceAdmin struct{ db *sqlx.DB }

func NewInstanceAdmin(db *sqlx.DB) *InstanceAdmin { return &InstanceAdmin{db: db} }

const instanceAdminColumns = `SELECT id,self_hosted,display_settings,google_client_id,apple_client_id,email_settings,
 email_password_encrypted,email_revision,diagnostic_results,integration_settings
 FROM instance_instanceconfiguration ORDER BY id LIMIT 1`

// Read is InstanceConfiguration.get_config(); nil when there is no row.
func (r *InstanceAdmin) Read(ctx context.Context) (*domain.InstanceAdminRow, error) {
	var row domain.InstanceAdminRow
	if err := r.db.GetContext(ctx, &row, instanceAdminColumns); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, nil
		}
		return nil, err
	}
	return &row, nil
}

// Locked is a transaction.atomic block around
// InstanceConfiguration.objects.select_for_update().first(): fn sees the
// locked row (nil without one) and returns the save to apply, if any. The
// block commits when fn returns no error (a 400/409 response commits nothing).
func (r *InstanceAdmin) Locked(ctx context.Context, fn func(*domain.InstanceAdminRow) (*domain.InstanceAdminUpdate, error)) error {
	tx, err := r.db.BeginTxx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var row domain.InstanceAdminRow
	var locked *domain.InstanceAdminRow
	if err = tx.GetContext(ctx, &row, instanceAdminColumns+` FOR UPDATE`); err == nil {
		locked = &row
	} else if !errors.Is(err, sql.ErrNoRows) {
		return err
	}
	update, err := fn(locked)
	if err != nil {
		return err
	}
	if update != nil && locked != nil {
		if err = applyInstanceAdminUpdate(ctx, tx, locked.ID, update); err != nil {
			return err
		}
	}
	return tx.Commit()
}

func applyInstanceAdminUpdate(ctx context.Context, tx *sqlx.Tx, id int64, update *domain.InstanceAdminUpdate) error {
	sets, args := "", []any{}
	add := func(column string, value any) {
		if sets != "" {
			sets += ","
		}
		args = append(args, value)
		sets += column + "=$" + strconv.Itoa(len(args))
	}
	if update.EmailSettings != nil {
		add("email_settings", string(update.EmailSettings))
	}
	if update.EmailPassword != nil {
		add("email_password_encrypted", *update.EmailPassword)
	}
	if update.EmailRevision != nil {
		add("email_revision", *update.EmailRevision)
	}
	if update.DiagnosticResults != nil {
		add("diagnostic_results", string(update.DiagnosticResults))
	}
	if update.IntegrationSettings != nil {
		add("integration_settings", string(update.IntegrationSettings))
	}
	if update.UpdatedAt != nil {
		add("updated_at", *update.UpdatedAt)
	}
	if sets == "" {
		return nil
	}
	args = append(args, id)
	_, err := tx.ExecContext(ctx, `UPDATE instance_instanceconfiguration SET `+sets+` WHERE id=$`+strconv.Itoa(len(args)), args...)
	return err
}

// UpdateBranding is InstanceAdminSerializer.update for the OEM fields and
// ui_api_version: save(update_fields=[...the edited fields..., 'updated_at']).
func (r *InstanceAdmin) UpdateBranding(ctx context.Context, fields map[string]string, now time.Time) error {
	sets, args := "updated_at=$1", []any{now}
	for _, column := range []string{"name", "abbreviation", "primary_color", "support_email", "ui_api_version"} {
		if value, ok := fields[column]; ok {
			args = append(args, value)
			sets += "," + column + "=$" + strconv.Itoa(len(args))
		}
	}
	_, err := r.db.ExecContext(ctx, `UPDATE instance_instanceconfiguration SET `+sets+` WHERE id=(SELECT id FROM instance_instanceconfiguration ORDER BY id LIMIT 1)`, args...)
	return err
}

// DiagnosticsLease is claim_operation's select_for_update(nowait=True)
// read: the stored diagnostic results, or ok=false when Django's
// DatabaseError path (lock not available, any database error) or a missing
// row makes the claim fail. Nothing is written.
func (r *InstanceAdmin) DiagnosticsLease(ctx context.Context) (results []byte, ok bool) {
	tx, err := r.db.BeginTxx(ctx, nil)
	if err != nil {
		return nil, false
	}
	defer tx.Rollback()
	var raw []byte
	if err = tx.GetContext(ctx, &raw, `SELECT diagnostic_results FROM instance_instanceconfiguration ORDER BY id LIMIT 1 FOR UPDATE NOWAIT`); err != nil {
		return nil, false
	}
	return raw, true
}

// UIAPIVersion is InstanceConfiguration.get_config().ui_api_version.
func (r *InstanceAdmin) UIAPIVersion(ctx context.Context) (string, error) {
	var version string
	err := r.db.GetContext(ctx, &version, `SELECT ui_api_version FROM instance_instanceconfiguration ORDER BY id LIMIT 1`)
	return version, err
}

// AppendUIAPIVersionChange is LogEntry.objects.log_create(config, action=UPDATE,
// actor=actor, changes={'ui_api_version': [previous, new]},
// additional_data={'actor_id': actor}, force_log=True) with its AuditLogIndex
// row (the same entry shape as backups.audit).
func (r *InstanceAdmin) AppendUIAPIVersionChange(ctx context.Context, change domain.InstanceUIAPIVersionChange) error {
	logs := &InstanceBackups{db: r.db}
	target, err := logs.LogTarget(ctx)
	if err != nil {
		return err
	}
	if target == nil {
		return errors.New("instance configuration missing")
	}
	changes, err := json.Marshal(map[string][]string{"ui_api_version": {change.Previous, change.Version}})
	if err != nil {
		return err
	}
	additional, err := json.Marshal(map[string]string{"actor_id": change.ActorID})
	if err != nil {
		return err
	}
	return logs.AppendBackupAction(ctx, InstanceBackupAction{Target: *target, ActorID: change.ActorID, Changes: changes,
		Additional: additional, Audit: change.Audit, At: change.At})
}
