package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
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
	// IndexFallback resolves the index association like the audit index
	// resolver's fallbacks instead of using AssociationID/ResolutionPath: the
	// resolver could not reload the row.
	IndexFallback bool
	At            time.Time
	// ObjectID is auditlog's object_id: the key of an integer primary key, else NULL.
	ObjectID *int64
}

func appendModelAudit(ctx context.Context, tx *sqlx.Tx, event modelAuditEntry) error {
	return appendModelAuditForApp(ctx, tx, "application", event)
}

// Content types are scoped by both Django application and model. Keep existing
// callers on application while allowing communications to share audit storage.
func appendModelAuditForApp(ctx context.Context, tx *sqlx.Tx, appLabel string, event modelAuditEntry) error {
	changes, err := json.Marshal(event.Changes)
	if err != nil {
		return err
	}
	var additionalData any
	if event.Context.AdditionalData != nil {
		encoded, err := json.Marshal(event.Context.AdditionalData)
		if err != nil {
			return err
		}
		additionalData = string(encoded)
	}
	var id int64
	// Preserve Python's arbitrary-width integer until the database applies the
	// existing positive-int4 column constraints at the actual audit write.
	var remotePort any
	if event.Context.RemotePort != nil {
		remotePort = string(*event.Context.RemotePort)
	}
	// QueryExecModeExec infers []byte as bytea; pass JSON text explicitly so
	// the existing jsonb cast works with transaction-pooler-safe connections.
	err = tx.GetContext(ctx, &id, `INSERT INTO auditlog_logentry
 (content_type_id,object_pk,object_id,object_repr,serialized_data,action,changes_text,changes,
 actor_id,cid,remote_addr,remote_port,timestamp,additional_data,actor_email)
 SELECT id,$1,$14::bigint,$2,NULL,$10,'',$3::jsonb,$4::uuid,$5,$6::inet,$7::text::integer,$8,$12::jsonb,$9
 FROM django_content_type WHERE app_label=$13 AND model=$11 RETURNING id`,
		event.Key, event.Representation, string(changes), event.Context.ActorID, event.Context.CorrelationID, event.Context.RemoteAddress, remotePort, event.At, event.Context.ActorEmail, event.Action, event.Model, additionalData, appLabel, event.ObjectID)
	if errors.Is(err, sql.ErrNoRows) {
		return errors.New("audit model content type missing")
	}
	if err != nil {
		return err
	}
	association, resolutionPath := event.AssociationID, event.ResolutionPath
	if event.IndexFallback {
		resolution, err := auditIndexFallback(ctx, tx, event.Changes, event.Context.ActorID)
		if err != nil {
			return err
		}
		if resolution == nil {
			return nil
		}
		association, resolutionPath = &resolution.AssociationID, &resolution.Path
	}
	if association != nil {
		_, err = tx.ExecContext(ctx, `INSERT INTO application_auditlogindex
  (audit_log_index_id,log_entry_id,sport_association_id,resolution_path,created_at)
  VALUES($1,$2,$3,$4,$5)`, uuid.NewString(), id, association, resolutionPath, event.At)
	}
	return err
}

// sportAssociationActive is SportAssociation.objects' filter (ActiveManager:
// deleted=False). The model has no Meta.ordering, so .first() orders by the
// primary key.
const sportAssociationActive = ` AND NOT deleted`

// auditIndexFallback is the index resolver's fallbacks for a log entry whose
// row the resolver cannot reload: changes['sport_association'] (old value then
// new value, skipping empty and 'None' values), else the actor's association.
// Both are SportAssociation.objects lookups, so soft-deleted rows never match.
func auditIndexFallback(ctx context.Context, q sqlx.QueryerContext, changes map[string][2]string, actor *string) (*AuditLogIndexResolution, error) {
	candidates, resolvable := auditIndexChangesCandidates(changes)
	if resolvable {
		for _, id := range candidates {
			var association string
			err := sqlx.GetContext(ctx, q, &association, `SELECT sport_association_id::text FROM application_sportassociation
 WHERE sport_association_id=$1::uuid`+sportAssociationActive+` ORDER BY sport_association_id LIMIT 1`, id)
			if err == nil {
				return &AuditLogIndexResolution{AssociationID: association, Path: "changes_json.sport_association"}, nil
			}
			if !errors.Is(err, sql.ErrNoRows) {
				return nil, err
			}
		}
	}
	if actor == nil || *actor == "" {
		return nil, nil
	}
	id, valid := dto.AuditIndexUUIDText(*actor)
	if !valid {
		// The field lookup raises, which the Django handler catches: no row.
		return nil, nil
	}
	var association string
	err := sqlx.GetContext(ctx, q, &association, `SELECT sport_association_id::text FROM application_sportassociation
 WHERE user_id=$1::uuid`+sportAssociationActive+` ORDER BY sport_association_id LIMIT 1`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &AuditLogIndexResolution{AssociationID: association, Path: "actor.sport_association"}, nil
}

// auditIndexChangesCandidates is the sport_association values the changes
// fallback tries, in order, as UUID text: empty and 'none' values are skipped
// (changes values are the strings auditlog writes, 'None' for None). A value
// the UUID field rejects raises inside the changes block, whose except clause
// ends it: the values before it are still tried, the ones after it are not,
// and the actor fallback follows.
func auditIndexChangesCandidates(changes map[string][2]string) (candidates []string, resolvable bool) {
	pair, ok := changes["sport_association"]
	if !ok {
		return nil, true
	}
	for _, value := range pair {
		if value == "" || strings.ToLower(value) == "none" {
			continue
		}
		id, valid := dto.AuditIndexUUIDText(value)
		if !valid {
			break
		}
		candidates = append(candidates, id)
	}
	return candidates, true
}
