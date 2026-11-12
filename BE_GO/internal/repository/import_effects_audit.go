package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/importeffects"
	"github.com/jmoiron/sqlx"
)

// ImportEffectsAudit writes one django-auditlog LogEntry outside a request
// (no actor, correlation id or remote address, as in a Celery task) and then
// create_audit_log_index's AuditLogIndex row for it.
func ImportEffectsAudit(ctx context.Context, tx *sqlx.Tx, entry importeffects.LogEntry) error {
	changes, err := json.Marshal(entry.Changes)
	if err != nil {
		return err
	}
	var id int64
	err = tx.GetContext(ctx, &id, `INSERT INTO auditlog_logentry
 (content_type_id,object_pk,object_id,object_repr,serialized_data,action,changes_text,changes,
 actor_id,cid,remote_addr,remote_port,timestamp,additional_data,actor_email)
 SELECT id,$1,$2::bigint,$3,NULL,$4,'',$5::jsonb,NULL,NULL,NULL,NULL,$6,NULL,NULL
 FROM django_content_type WHERE app_label=$7 AND model=$8 RETURNING id`,
		entry.ObjectPK, entry.ObjectID, entry.Repr, entry.Action, string(changes), entry.At, entry.App, entry.Model)
	if errors.Is(err, sql.ErrNoRows) {
		return errors.New("audit model content type missing")
	}
	if err != nil {
		return err
	}
	_, err = IndexAuditLogEntry(ctx, tx, id, "", entry.At)
	return err
}
