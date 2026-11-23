package celeryworker

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/handoff"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/smtpsend"
	"github.com/google/uuid"
)

// BrokerHandoff serializes freeze acknowledgement with entry to a Redis reservation.
type BrokerHandoff struct {
	DB                *sql.DB
	Interval          time.Duration
	id                string
	acknowledged      string
	nextSMTPRecovery  time.Time
	mu                sync.Mutex
	active, reserving bool
}

func (h *BrokerHandoff) heartbeat(ctx context.Context) {
	var owner, admission string
	var op sql.NullString
	err := h.DB.QueryRowContext(ctx, `SELECT runtime, admission, operation_id FROM
 public.assozeta_handoff_heartbeat($1,'go','broker')`, h.id).Scan(&owner, &admission, &op)
	if now := time.Now(); err == nil && owner == "go" && admission == "open" && !now.Before(h.nextSMTPRecovery) {
		h.nextSMTPRecovery = now.Add(time.Second)
		h.recoverSMTP(ctx)
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	h.active = err == nil && owner == "go" && admission == "open"
	if err == nil && owner == "go" && op.Valid && op.String != h.acknowledged && !h.reserving {
		var outcome string
		if err := h.DB.QueryRowContext(ctx, "SELECT public.assozeta_handoff_acknowledge_freeze($1,$2)", h.id, op.String).Scan(&outcome); err == nil && outcome == "acknowledged" {
			h.acknowledged = op.String
			handoff.Barrier("freeze:acknowledged", h.id)
		}
	}
}

func (h *BrokerHandoff) recoverSMTP(ctx context.Context) {
	// Early-acked SMTP tasks can lose their broker delivery before storing an
	// outcome. The expired lease and unresolved intent suffice to block them.
	_, _ = h.DB.ExecContext(ctx, `WITH owner AS MATERIALIZED (
    SELECT generation FROM public.assozeta_go_job_ownership
    WHERE singleton=1 AND runtime='go' AND admission='open' FOR SHARE
), expired AS MATERIALIZED (
    SELECT a.attempt_id FROM owner o
    JOIN public.assozeta_handoff_work w ON w.owner_runtime='go' AND w.owner_generation=o.generation
        AND w.state='running' AND w.transport='broker'
    JOIN public.assozeta_handoff_attempts a ON a.task_id=w.task_id AND a.runtime=w.owner_runtime
        AND a.generation=w.owner_generation
    WHERE a.outcome='running' AND a.lease_until<=clock_timestamp()
      AND EXISTS (SELECT 1 FROM public.assozeta_handoff_effects e
                  WHERE e.attempt_id=a.attempt_id AND e.kind='smtp' AND e.state='intent')
    ORDER BY a.lease_until LIMIT 100 FOR UPDATE OF a SKIP LOCKED
)
SELECT public.assozeta_handoff_finish(attempt_id,'released') FROM expired`)
}

func (h *BrokerHandoff) start(ctx context.Context) {
	h.id = "go-broker-" + uuid.NewString()
	h.heartbeat(ctx)
	go func() {
		ticker := time.NewTicker(h.Interval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				h.heartbeat(ctx)
			}
		}
	}()
}

func (h *BrokerHandoff) reserve() bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.reserving = h.active
	return h.active
}

func (h *BrokerHandoff) reserved() {
	h.mu.Lock()
	h.reserving = false
	h.mu.Unlock()
}

type attemptContext struct{}
type brokerAttempt struct {
	stopRenewal context.CancelFunc
	ledger      *BrokerHandoff
	id, task    string
	retries     int64
}

func (w *Worker) claim(ctx context.Context, d *Delivery) (context.Context, bool) {
	h := w.options.Handoff
	var outcome string
	var attempt sql.NullString
	err := h.DB.QueryRowContext(ctx, `SELECT outcome,attempt_id FROM public.assozeta_handoff_claim('go',$1,$2,$3,$4,$5,$6)`,
		h.id, d.Message.ID, d.Message.Task, d.Raw, d.Message.DeliveryTag, d.Message.Redelivered()).Scan(&outcome, &attempt)
	if err != nil {
		return ctx, false
	} // unacked for visibility restore
	if outcome != "claimed" {
		if outcome == "active" {
			// Leave it unacked for visibility restore while the original runs.
			return ctx, false
		}
		handoff.Barrier("claim:refused", d.Message.ID)
		if outcome == "expired" {
			w.revoke(ctx, d)
		} else {
			if outcome == "frozen" || outcome == "not_owner" {
				// An adopted copy can be acknowledged even if the process resumed after
				// staging. Keep an unadopted legacy item in Kombu's unacked store.
				var durable bool
				if err := h.DB.QueryRowContext(ctx, "SELECT EXISTS(SELECT 1 FROM public.assozeta_handoff_work WHERE task_id=$1)", d.Message.ID).Scan(&durable); err != nil || !durable {
					return ctx, false
				}
			}
			w.ack(ctx, d)
		}
		return ctx, false
	}
	a := &brokerAttempt{ledger: h, id: attempt.String, task: d.Message.ID, retries: d.Message.Retries}
	a.renew(ctx)
	handoff.Barrier("claim:granted", d.Message.ID)
	_, err = h.DB.ExecContext(ctx, "SELECT public.assozeta_handoff_fence($1)", a.id)
	if err != nil {
		a.stopRenewal()
		handoff.Barrier("attempt:fenced", a.task)
		return ctx, false
	}
	return a.context(ctx), true
}

func (w *Worker) blockDelivery(ctx context.Context, d *Delivery, reason string) taskOutcome {
	if reason == "input" || reason == "options" {
		reason = "unsupported_" + reason
	}
	var err error
	if d.Message == nil {
		err = w.blockUndecodable(ctx, d, reason)
		if err != nil {
			err = errors.Join(err, w.transport.RestoreUndecodable(ctx, d))
		}
	} else {
		attempt, ok := ctx.Value(attemptContext{}).(*brokerAttempt)
		if !ok {
			claimed, ok := w.claim(ctx, d)
			if !ok {
				return outcomeNone
			}
			attempt = claimed.Value(attemptContext{}).(*brokerAttempt)
			defer attempt.stopRenewal()
		}
		err = attempt.block(ctx, reason)
		if err == nil {
			w.ack(ctx, d)
		}
	}
	if err != nil {
		w.logger.Warn("celery delivery could not be blocked", "code", "celery_block_failed", "reason", reason)
		return outcomeNone
	}
	return outcomeBlocked
}

func (a *brokerAttempt) block(ctx context.Context, reason string) error {
	tx, err := a.ledger.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err := tx.ExecContext(ctx, "SELECT public.assozeta_handoff_finish($1,'released')", a.id); err != nil {
		return err
	}
	if _, err := tx.ExecContext(ctx, `UPDATE public.assozeta_handoff_work
SET state='blocked',block_reason=$2,updated_at=clock_timestamp() WHERE task_id=$1 AND state='pending'`, a.task, reason); err != nil {
		return err
	}
	return tx.Commit()
}

func (w *Worker) blockUndecodable(ctx context.Context, d *Delivery, reason string) error {
	var raw struct {
		Headers struct {
			ID   string `json:"id"`
			Task string `json:"task"`
		} `json:"headers"`
	}
	_ = json.Unmarshal(d.Raw, &raw)
	id, task := raw.Headers.ID, raw.Headers.Task
	fallback := fmt.Sprintf("undecodable:%x", sha256.Sum256(d.Raw))
	if id == "" || len(id) > 255 || strings.ContainsRune(id, 0) {
		id = fallback
	}
	if task == "" || len(task) > 255 || strings.ContainsRune(task, 0) {
		task = "undecodable"
	}
	// Preserve the raw bytes even when no task identity or usable metadata
	// survived decoding. A conflicting legacy ID gets a separate ledger row.
	var recorded bool
	err := w.options.Handoff.DB.QueryRowContext(ctx, `
WITH owner AS MATERIALIZED (
    SELECT runtime,generation FROM public.assozeta_go_job_ownership
    WHERE singleton=1 AND runtime='go' AND admission='open' FOR SHARE
), saved AS (
    INSERT INTO public.assozeta_handoff_work AS w
        (task_id,task_name,transport,queue,envelope,origin_runtime,owner_runtime,owner_generation,state,accepted_at,block_reason)
    SELECT CASE WHEN EXISTS(SELECT 1 FROM public.assozeta_handoff_work
                            WHERE task_id=$1 AND envelope IS DISTINCT FROM $4) THEN $2 ELSE $1 END,
           $3,'broker',$5,$4,runtime,runtime,generation,'blocked',clock_timestamp(),$6
    FROM owner
    ON CONFLICT (task_id) DO UPDATE SET state='blocked',block_reason=excluded.block_reason,updated_at=clock_timestamp()
    WHERE w.owner_runtime=excluded.owner_runtime AND w.owner_generation=excluded.owner_generation
        AND w.envelope=excluded.envelope AND w.state='pending'
    RETURNING 1
)
SELECT EXISTS(SELECT 1 FROM saved) OR EXISTS(
    SELECT 1 FROM public.assozeta_handoff_work w,owner
    WHERE w.task_id IN ($1,$2) AND w.envelope=$4 AND w.state IN ('blocked','completed','failed','expired','revoked','cancelled')
)`, id, fallback, task, d.Raw, w.transport.Queue, reason).Scan(&recorded)
	if err == nil && !recorded {
		return errors.New("undecodable delivery was not recorded")
	}
	return err
}

// Renew on the ordinary pool, independently of a blocking task. Short tasks
// finish before the first tick and pay no renewal query.
func (a *brokerAttempt) renew(ctx context.Context) {
	ctx, a.stopRenewal = context.WithCancel(ctx)
	go func() {
		ticker := time.NewTicker(10 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				query, cancel := context.WithTimeout(ctx, 5*time.Second)
				_, _ = a.ledger.DB.ExecContext(query, "SELECT public.assozeta_handoff_renew(ARRAY[$1::uuid])", a.id)
				cancel()
			}
		}
	}()
}

func (a *brokerAttempt) context(ctx context.Context) context.Context {
	return smtpsend.WithLedgerAttempt(context.WithValue(ctx, attemptContext{}, a), a.ledger.DB, a.task, a.id, a.retries)
}

// BrokerConsumerID identifies the registered worker that claimed this task.
func BrokerConsumerID(ctx context.Context) string {
	if attempt, ok := ctx.Value(attemptContext{}).(*brokerAttempt); ok {
		return attempt.ledger.id
	}
	return ""
}

func HasBrokerAttempt(ctx context.Context) bool {
	return ctx.Value(attemptContext{}) != nil
}

func (a *brokerAttempt) finish(ctx context.Context, result taskOutcome) {
	outcome := map[taskOutcome]string{outcomeSuccess: "completed", outcomeRetry: "retried", outcomeFailure: "failed", outcomeHandedOff: "released"}[result]
	if outcome == "" {
		return
	}
	_, err := a.ledger.DB.ExecContext(ctx, `SELECT public.assozeta_handoff_finish($1,$2) WHERE EXISTS
 (SELECT 1 FROM public.assozeta_handoff_attempts WHERE attempt_id=$1 AND outcome='running')`, a.id, outcome)
	if err != nil {
		handoff.Barrier("attempt:fenced", a.task)
	}
}
