package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// Concrete model-specific repository methods supply this persistence record.
// Services still own change semantics, attribution and transaction boundaries.
type modelAuditEntry struct {
	Model, Key, Representation    string
	Action                        int
	Changes                       map[string][2]string
	Context                       domain.LoginAuditContext
	AssociationID, ResolutionPath *string
	At                            time.Time
}

func appendModelAudit(ctx context.Context, tx *sqlx.Tx, event modelAuditEntry) error {
	changes, err := json.Marshal(event.Changes)
	if err != nil {
		return err
	}
	var id int64
	// Preserve Python's arbitrary-width integer until the database applies the
	// existing positive-int4 column constraints at the actual audit write.
	var remotePort any
	if event.Context.RemotePort != nil {
		remotePort = string(*event.Context.RemotePort)
	}
	err = tx.GetContext(ctx, &id, `INSERT INTO auditlog_logentry
 (content_type_id,object_pk,object_id,object_repr,serialized_data,action,changes_text,changes,
 actor_id,cid,remote_addr,remote_port,timestamp,additional_data,actor_email)
 SELECT id,$1,NULL,$2,NULL,$10,'',$3::jsonb,$4::uuid,$5,$6::inet,$7::text::integer,$8,NULL,$9
 FROM django_content_type WHERE app_label='application' AND model=$11 RETURNING id`,
		event.Key, event.Representation, changes, event.Context.ActorID, event.Context.CorrelationID, event.Context.RemoteAddress, remotePort, event.At, event.Context.ActorEmail, event.Action, event.Model)
	if errors.Is(err, sql.ErrNoRows) {
		return errors.New("audit model content type missing")
	}
	if err != nil {
		return err
	}
	if event.AssociationID != nil {
		_, err = tx.ExecContext(ctx, `INSERT INTO application_auditlogindex
  (audit_log_index_id,log_entry_id,sport_association_id,resolution_path,created_at)
  VALUES($1,$2,$3,$4,$5)`, uuid.NewString(), id, event.AssociationID, event.ResolutionPath, event.At)
	}
	return err
}
