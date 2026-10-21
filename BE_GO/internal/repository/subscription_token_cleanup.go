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

type SubscriptionTokenCleanups struct{ db *sqlx.DB }

func NewSubscriptionTokenCleanups(db *sqlx.DB) *SubscriptionTokenCleanups {
	return &SubscriptionTokenCleanups{db: db}
}
func (r *SubscriptionTokenCleanups) BeginSubscriptionTokenCleanup(ctx context.Context) (domain.SubscriptionTokenCleanupTransaction, error) {
	// Completion must see the current lease after concurrent heartbeat updates.
	tx, err := r.db.BeginTxx(ctx, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &subscriptionTokenCleanupTransaction{tx: tx}, nil
}

type subscriptionTokenCleanupTransaction struct{ tx *sqlx.Tx }

// DeleteExpiredWithAudit performs the service's explicit audited deletion command.
// One statement fixes the qualifying set and derives audits from rows actually
// deleted. No growing rowset is loaded into Go and concurrent cleanup cannot emit
// an audit for a token that another execution already removed.
func (t *subscriptionTokenCleanupTransaction) DeleteExpiredWithAudit(ctx context.Context, cutoff, at time.Time) (int64, error) {
	var counts struct{ Removed, Logged int64 }
	err := t.tx.GetContext(ctx, &counts, deleteExpiredSubscriptionTokensSQL, cutoff, at)
	if err != nil {
		return 0, err
	}
	if counts.Removed != counts.Logged {
		return 0, errors.New("subscription token audit content type missing")
	}
	return counts.Removed, nil
}

// Background post-delete audit has no actor or direct association fallback, so
// Django does not create an AuditLogIndex. Timestamps in changes are naive UTC
// strings with six fractional digits only when microseconds are nonzero.
const deleteExpiredSubscriptionTokensSQL = `WITH removed AS (
 DELETE FROM application_subscriptiontoken WHERE expiration_date<$1
 RETURNING subscription_token_id,subscription_id,token,creation_date,expiration_date
), logged AS (
 INSERT INTO auditlog_logentry
 (content_type_id,object_pk,object_id,object_repr,serialized_data,action,changes_text,changes,
 actor_id,cid,remote_addr,remote_port,timestamp,additional_data,actor_email)
 SELECT ct.id,r.subscription_token_id::text,NULL,'SubscriptionToken object ('||r.subscription_token_id::text||')',NULL,2,'',
 jsonb_build_object(
 'subscription_token_id',jsonb_build_array(r.subscription_token_id::text,'None'),
 'subscription',jsonb_build_array(r.subscription_id::text,'None'),
 'token',jsonb_build_array(r.token::text,'None'),
 'creation_date',jsonb_build_array(replace(to_char(r.creation_date AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS.US'),'.000000',''),'None'),
 'expiration_date',jsonb_build_array(replace(to_char(r.expiration_date AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS.US'),'.000000',''),'None')
 ),NULL,NULL,NULL,NULL,$2,NULL,NULL
 FROM removed r CROSS JOIN django_content_type ct
 WHERE ct.app_label='application' AND ct.model='subscriptiontoken'
 RETURNING id
)
SELECT (SELECT count(*) FROM removed) AS removed,(SELECT count(*) FROM logged) AS logged`

func (t *subscriptionTokenCleanupTransaction) CompleteJob(ctx context.Context, id, token uuid.UUID) error {
	return completeJob(ctx, t.tx, id, token, nil)
}
func (t *subscriptionTokenCleanupTransaction) Commit() error   { return t.tx.Commit() }
func (t *subscriptionTokenCleanupTransaction) Rollback() error { return t.tx.Rollback() }
