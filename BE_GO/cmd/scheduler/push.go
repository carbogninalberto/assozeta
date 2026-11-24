package main

import (
	"context"
	"database/sql"
	"errors"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/redis/go-redis/v9"
)

// A Redis marker makes retry after LPUSH but before the SQL commit harmless.
// Remove it only after SQL has acknowledged the push.
func (t *authorityTicker) pushPending(ctx context.Context, lease repository.ScheduleLease) error {
	for {
		id, err := t.pushOne(ctx, lease)
		if err != nil || id == "" {
			return err
		}
		if err = t.broker.Del(ctx, "assozeta:schedule:published:"+id).Err(); err != nil {
			return err
		}
	}
}
func (t *authorityTicker) pushOne(ctx context.Context, lease repository.ScheduleLease) (string, error) {
	tx, err := t.db.BeginTxx(ctx, nil)
	if err != nil {
		return "", err
	}
	defer tx.Rollback()
	var owner int64
	if err = tx.GetContext(ctx, &owner, "SELECT public.assozeta_scheduler_fence($1,$2)", lease.Holder, lease.Generation); err != nil {
		return "", err
	}
	var id, queue string
	var body []byte
	err = tx.QueryRowContext(ctx, `SELECT w.task_id,w.queue,w.envelope FROM public.assozeta_go_periodic_occurrences r
        JOIN public.assozeta_handoff_work w USING(task_id)
        WHERE r.runtime='go' AND r.ownership_generation=$1 AND r.job_id IS NULL AND r.broker_pushed_at IS NULL
          AND w.owner_runtime='go' AND w.owner_generation=$1 AND w.state='pending'
        ORDER BY r.dispatched_at,r.id LIMIT 1 FOR UPDATE OF r`, owner).Scan(&id, &queue, &body)
	if errors.Is(err, sql.ErrNoRows) {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	err = pushOnce.Run(ctx, t.broker, []string{queue, "assozeta:schedule:published:" + id, "_kombu.binding." + queue}, body, queue+"\x06\x16\x06\x16"+queue).Err()
	if err != nil {
		return "", err
	}
	_, err = tx.ExecContext(ctx, `UPDATE public.assozeta_go_periodic_occurrences SET broker_pushed_at=clock_timestamp() WHERE task_id=$1`, id)
	if err != nil {
		return "", err
	}
	return id, tx.Commit()
}

var pushOnce = redis.NewScript(`
if redis.call('exists',KEYS[2])==0 then
    redis.call('sadd',KEYS[3],ARGV[2])
    redis.call('lpush',KEYS[1],ARGV[1])
    redis.call('set',KEYS[2],'1')
end
return 1`)
