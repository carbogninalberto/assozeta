package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

type AssociateAgeUpdates struct{ db *sqlx.DB }

func NewAssociateAgeUpdates(db *sqlx.DB) *AssociateAgeUpdates { return &AssociateAgeUpdates{db: db} }
func (r *AssociateAgeUpdates) BeginAssociateAgeUpdate(ctx context.Context) (domain.AssociateAgeUpdateTransaction, error) {
	tx, err := r.db.BeginTxx(ctx, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &associateAgeTransaction{tx: tx}, nil
}

type associateAgeTransaction struct{ tx *sqlx.Tx }

func (t *associateAgeTransaction) PromoteAdultsWithAudit(ctx context.Context, bornOnOrBefore, at time.Time) (int64, error) {
	var counts struct{ Changed, Logged, Indexed, ExpectedIndexes int64 }
	err := t.tx.GetContext(ctx, &counts, updateAssociateAgeSQL, bornOnOrBefore, at)
	if err != nil {
		return 0, err
	}
	if counts.Changed != counts.Logged || counts.Indexed != counts.ExpectedIndexes {
		return 0, errors.New("associate age audit/index count mismatch")
	}
	return counts.Changed, nil
}

// Only changed rows produce audit records. The direct association resolver also
// indexes deleted associations; a NULL association has no background fallback.
// The shared statement snapshot and READ COMMITTED completion permit heartbeat
// renewal without loading the candidate rowset into Go.
const updateAssociateAgeSQL = `WITH changed AS (
 UPDATE application_associate SET is_minor=FALSE
 WHERE is_minor=TRUE AND deleted=FALSE AND born_date<=$1::date
 RETURNING associate_id,sport_association_id
), logged AS (
 INSERT INTO auditlog_logentry
 (content_type_id,object_pk,object_id,object_repr,serialized_data,action,changes_text,changes,
 actor_id,cid,remote_addr,remote_port,timestamp,additional_data,actor_email)
 SELECT ct.id,c.associate_id::text,NULL,'Associate object ('||c.associate_id::text||')',NULL,1,'',
 '{"is_minor":["True","False"]}'::jsonb,NULL,NULL,NULL,NULL,$2,NULL,NULL
 FROM changed c CROSS JOIN django_content_type ct
 WHERE ct.app_label='application' AND ct.model='associate'
 RETURNING id,object_pk
), indexed AS (
 INSERT INTO application_auditlogindex
 (audit_log_index_id,log_entry_id,sport_association_id,resolution_path,created_at)
 SELECT gen_random_uuid(),l.id,c.sport_association_id,'direct',$2
 FROM logged l JOIN changed c ON l.object_pk=c.associate_id::text
 WHERE c.sport_association_id IS NOT NULL
 RETURNING audit_log_index_id
)
SELECT (SELECT count(*) FROM changed) AS changed,(SELECT count(*) FROM logged) AS logged,
 (SELECT count(*) FROM indexed) AS indexed,
 (SELECT count(*) FROM changed WHERE sport_association_id IS NOT NULL) AS expectedindexes`

func (t *associateAgeTransaction) CompleteJob(ctx context.Context, id, token uuid.UUID) error {
	return completeJob(ctx, t.tx, id, token, nil)
}
func (t *associateAgeTransaction) Commit() error   { return t.tx.Commit() }
func (t *associateAgeTransaction) Rollback() error { return t.tx.Rollback() }
