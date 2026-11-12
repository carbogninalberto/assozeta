package repository

import (
	"context"
	"database/sql"
	"errors"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

// InstanceBackupAction is backups.audit(actor, action, value, **extra):
// LogEntry.objects.log_create(config, action=UPDATE, actor=actor,
// changes={action: [”, str(value)]}, additional_data={'actor_id': actor,
// **extra}, force_log=True). Changes and Additional are the JSON documents.
type InstanceBackupAction struct {
	Target     InstanceBackupsLogTarget
	ActorID    string
	Changes    []byte
	Additional []byte
	Audit      domain.LoginAuditContext
	At         time.Time
}

// AppendBackupAction writes the UPDATE entry outside any transaction, then
// the AuditLogIndex signal's row: no resolver for the configuration and no
// sport_association in the changes, so the actor's first active association
// (best effort, as the signal swallows its errors).
func (r *InstanceBackups) AppendBackupAction(ctx context.Context, event InstanceBackupAction) error {
	var remotePort any
	if event.Audit.RemotePort != nil {
		remotePort = string(*event.Audit.RemotePort)
	}
	var id int64
	err := r.db.GetContext(ctx, &id, `INSERT INTO auditlog_logentry
 (content_type_id,object_pk,object_id,object_repr,serialized_data,action,changes_text,changes,actor_id,cid,remote_addr,remote_port,timestamp,additional_data,actor_email)
 SELECT id,$1,$2,$3,NULL,1,'',$4::jsonb,$5::uuid,$6,$7::inet,$8::text::integer,$9,$10::jsonb,NULL
 FROM django_content_type WHERE app_label='instance' AND model='instanceconfiguration' RETURNING id`,
		strconv.FormatInt(event.Target.ID, 10), event.Target.ID, "Instance: "+event.Target.Name+" ("+event.Target.Domain+")",
		string(event.Changes), event.ActorID, event.Audit.CorrelationID, event.Audit.RemoteAddress, remotePort, event.At,
		string(event.Additional))
	if errors.Is(err, sql.ErrNoRows) {
		return errors.New("audit model content type missing")
	}
	if err != nil {
		return err
	}
	var indexed bool
	if err = r.db.GetContext(ctx, &indexed, `SELECT EXISTS(SELECT 1 FROM application_auditlogindex WHERE log_entry_id=$1)`, id); err != nil {
		return err
	}
	if indexed {
		return nil
	}
	var association string
	err = r.db.GetContext(ctx, &association, `SELECT sport_association_id::text FROM application_sportassociation
 WHERE user_id=$1::uuid AND NOT deleted ORDER BY sport_association_id LIMIT 1`, event.ActorID)
	if err == nil {
		_, _ = r.db.ExecContext(ctx, `INSERT INTO application_auditlogindex(audit_log_index_id,log_entry_id,sport_association_id,resolution_path,created_at)
 VALUES($1,$2,$3,'actor.sport_association',$4)`, uuid.NewString(), id, association, event.At)
	}
	return nil
}
