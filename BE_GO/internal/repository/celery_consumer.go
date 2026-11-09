package repository

import (
	"context"
	"time"

	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// CeleryResults is django_celery_results' DatabaseBackend with
// result_extended off: TaskResultManager.store_result's get_or_create by
// task_id, then save() of every field on an existing row (date_created
// kept, date_done refreshed).
type CeleryResults struct{ db *sqlx.DB }

func NewCeleryResults(db *sqlx.DB) *CeleryResults { return &CeleryResults{db: db} }

func (r *CeleryResults) StoreResult(ctx context.Context, taskID, status, result, meta string) error {
	now := time.Now().UTC().Truncate(time.Microsecond)
	_, err := r.db.ExecContext(ctx, `INSERT INTO django_celery_results_taskresult
 (task_id, periodic_task_name, task_name, task_args, task_kwargs, status, worker, content_type, content_encoding, result,
  date_created, date_done, traceback, meta)
 VALUES ($1, NULL, NULL, NULL, NULL, $2, NULL, 'application/json', 'utf-8', $3, $4, $4, NULL, $5)
 ON CONFLICT (task_id) DO UPDATE SET periodic_task_name=NULL, task_name=NULL, task_args=NULL, task_kwargs=NULL,
  status=EXCLUDED.status, worker=NULL, content_type=EXCLUDED.content_type, content_encoding=EXCLUDED.content_encoding,
  result=EXCLUDED.result, date_done=EXCLUDED.date_done, traceback=NULL, meta=EXCLUDED.meta`, taskID, status, result, now, meta)
	return err
}

// SendMailAsyncStore holds the reads and the EmailLog write of
// core.tasks.send_mail_async; each statement autocommits as in Django.
type SendMailAsyncStore struct{ db *sqlx.DB }

func NewSendMailAsyncStore(db *sqlx.DB) *SendMailAsyncStore { return &SendMailAsyncStore{db: db} }

// ActiveAssociation is SportAssociation.objects.filter(pk=id).first() (the
// ActiveManager excludes deleted rows).
func (r *SendMailAsyncStore) ActiveAssociation(ctx context.Context, id uuid.UUID) (bool, error) {
	var found bool
	err := r.db.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM application_sportassociation
 WHERE NOT deleted AND sport_association_id=$1)`, id)
	return found, err
}

// HasCommunicationConfiguration is CommunicationConfiguration.objects.filter(
// sport_association=association).first() is not None.
func (r *SendMailAsyncStore) HasCommunicationConfiguration(ctx context.Context, association uuid.UUID) (bool, error) {
	var found bool
	err := r.db.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM communications_communicationconfiguration
 WHERE sport_association_id=$1)`, association)
	return found, err
}

// InstanceEmail reports email_configuration.effective_email's instance
// source: InstanceConfiguration.objects.first() with self_hosted and truthy
// email_settings.
func (r *SendMailAsyncStore) InstanceEmail(ctx context.Context) (bool, error) {
	var instance bool
	err := r.db.GetContext(ctx, &instance, `SELECT COALESCE((SELECT self_hosted AND CASE jsonb_typeof(email_settings::jsonb)
   WHEN 'object' THEN email_settings::jsonb <> '{}'::jsonb WHEN 'array' THEN email_settings::jsonb <> '[]'::jsonb
   WHEN 'string' THEN email_settings::jsonb <> '""'::jsonb WHEN 'number' THEN email_settings::jsonb <> '0'::jsonb
   WHEN 'boolean' THEN email_settings::jsonb = 'true'::jsonb ELSE false END
 FROM instance_instanceconfiguration ORDER BY id LIMIT 1), false)`)
	return instance, err
}

// InsertEmailLog is EmailLog.objects.create(recipient, subject, result,
// sport_association).
func (r *SendMailAsyncStore) InsertEmailLog(ctx context.Context, recipient, subject, result string, association *uuid.UUID) error {
	_, err := r.db.ExecContext(ctx, `INSERT INTO application_emaillog (email_log_id, recipient, subject, result, sent_at, sport_association_id)
 VALUES ($1, $2, $3, $4, $5, $6)`, uuid.New(), recipient, subject, result, time.Now().UTC().Truncate(time.Microsecond), association)
	return err
}
