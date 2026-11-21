package repository

import (
	"context"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// The Celery producer serializes the normal Go envelope. Its broker admits
// that envelope into the same transaction as the occurrence and cursor.
type scheduledAdmission struct {
	tx   *sqlx.Tx
	task celery.Task
}

func (b scheduledAdmission) Push(ctx context.Context, queue string, payload []byte) error {
	var outcome string
	err := b.tx.GetContext(ctx, &outcome, `SELECT outcome FROM public.assozeta_handoff_admit(
        'go',$1,$2,'broker',$3,$4,clock_timestamp(),NULL,$5)`, b.task.ID, b.task.Name, queue, payload, b.task.Expires)
	if err == nil && outcome != "accepted" {
		return domain.ErrJobConflict
	}
	return err
}

func (t *occurrenceTransaction) admitBrokerOccurrence(ctx context.Context, id uuid.UUID) error {
	task := celery.Task{Name: t.source.Task, ID: id.String(), Queue: celery.GoQueue, Expires: optionalTime(t.source.Expires)}
	if t.source.ExpireSeconds.Valid {
		expiry := time.Now().UTC().Add(time.Second * time.Duration(t.source.ExpireSeconds.Int64))
		task.Expires = &expiry
	}
	_, err := celery.NewProducer(scheduledAdmission{t.tx, task}).Publish(ctx, task)
	return err
}
