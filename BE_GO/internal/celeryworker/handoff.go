package celeryworker

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
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
	mu                sync.Mutex
	active, reserving bool
}

func (h *BrokerHandoff) heartbeat(ctx context.Context) {
	var owner, admission string
	var op sql.NullString
	err := h.DB.QueryRowContext(ctx, `SELECT runtime, admission, operation_id FROM
 public.assozeta_handoff_heartbeat($1,'go','broker')`, h.id).Scan(&owner, &admission, &op)
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
	effects     int
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
	return smtpsend.WithEffectIntent(context.WithValue(ctx, attemptContext{}, a), a.intent)
}

func HasBrokerAttempt(ctx context.Context) bool {
	return ctx.Value(attemptContext{}) != nil
}

func (a *brokerAttempt) intent(ctx context.Context) (func(bool) error, error) {
	key := fmt.Sprintf("%s:%d:smtp:%d", a.task, a.retries, a.effects)
	a.effects++
	var outcome string
	err := a.ledger.DB.QueryRowContext(ctx, "SELECT public.assozeta_handoff_effect_intent($1,$2,'smtp')", a.id, key).Scan(&outcome)
	if err != nil || outcome == "confirmed" {
		return nil, err
	}
	if outcome != "recorded" {
		return nil, errors.New("SMTP effect needs reconciliation")
	}
	return func(accepted bool) error {
		if !accepted {
			_, err := a.ledger.DB.ExecContext(ctx, "DELETE FROM public.assozeta_handoff_effects WHERE effect_key=$1 AND attempt_id=$2 AND state='intent'", key, a.id)
			return err
		}
		handoff.Barrier("effect:accepted", a.task)
		_, err := a.ledger.DB.ExecContext(ctx, `SELECT public.assozeta_handoff_effect_confirm($1,NULL)
  FROM (SELECT public.assozeta_handoff_fence($2)) fenced`, key, a.id)
		return err
	}, nil
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
