package celery

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"time"
)

// AdmittingBroker commits an accepted envelope before touching Redis. A
// second authority lock orders the actual push against freeze and staging.
type AdmittingBroker struct {
	DB    *sql.DB
	Redis RedisBroker
}

func (b AdmittingBroker) Push(ctx context.Context, queue string, raw []byte) error {
	return b.PublishAttempt(ctx, queue, raw, "")
}

func (b AdmittingBroker) HandoffLedgerEnabled(ctx context.Context) (bool, error) {
	var installed bool
	if err := b.DB.QueryRowContext(ctx, "SELECT to_regclass('public.assozeta_handoff_work') IS NOT NULL").Scan(&installed); err != nil {
		return false, errors.New("cannot check task admission")
	}
	return installed, nil
}

func (b AdmittingBroker) PublishAttempt(ctx context.Context, queue string, raw []byte, attempt string) error {
	installed, err := b.HandoffLedgerEnabled(ctx)
	if err != nil {
		return err
	}
	if !installed {
		return b.Redis.Push(ctx, queue, raw)
	}
	// Admission only needs the protocol headers. Decoding a body here would
	// make publication depend on the target consumer's supported argument types.
	var envelope struct {
		Headers struct {
			ID, Task     string
			ETA, Expires *time.Time
			PublishedAt  float64 `json:"assozeta_published_at"`
		}
		Properties struct{ Priority int64 }
	}
	if err := json.Unmarshal(raw, &envelope); err != nil || envelope.Headers.ID == "" || envelope.Headers.Task == "" {
		return errors.New("invalid task envelope")
	}
	m := envelope.Headers
	tx, err := b.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if attempt != "" {
		if _, err = tx.ExecContext(ctx, `SELECT public.assozeta_handoff_finish($1,'retried') WHERE EXISTS
   (SELECT 1 FROM public.assozeta_handoff_attempts WHERE attempt_id=$1 AND outcome='running')`, attempt); err != nil {
			return errors.New("retry attempt fenced")
		}
	}
	stamp := time.Unix(0, int64(m.PublishedAt*1e9))
	var disposition string
	// Schema 8 provides the exclusive consumer for the managed broker queue.
	var exclusiveRoutes bool
	err = tx.QueryRowContext(ctx, `SELECT outcome, to_regclass('public.assozeta_outbox_effects') IS NOT NULL
        FROM public.assozeta_handoff_admit('go',$1,$2,'broker',$3,$4,$5,$6,$7)`, m.ID, m.Task, queue, raw, stamp, m.ETA, m.Expires).Scan(&disposition, &exclusiveRoutes)
	if err != nil {
		return errors.New("task admission failed")
	}
	if disposition == "rejected" {
		return errors.New("Go task admission rejected")
	}
	// Admission holds the authority lock. Record the same route in the ledger
	// and the envelope, including a retry inherited from the old queue.
	goRoute := exclusiveRoutes || ownedEmailTask(m.Task) || ownedWorkflowTask(m.Task) ||
		ownedStorageTask(m.Task) || ownedPrintTask(m.Task) || ownedAssociationDataTask(m.Task)
	if (disposition == "accepted" || disposition == "held") && queue == DefaultQueue && goRoute {
		raw, err = routeEnvelope(raw, GoQueue)
		if err != nil {
			return err
		}
		queue = GoQueue
		if _, err = tx.ExecContext(ctx, `UPDATE public.assozeta_handoff_work
            SET queue=$2,envelope=$3 WHERE task_id=$1`, m.ID, queue, raw); err != nil {
			return err
		}
	}
	if err = tx.Commit(); err != nil {
		return err
	}
	if disposition != "accepted" {
		return nil
	}
	dispatch, err := b.DB.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer dispatch.Rollback()
	var allowed bool
	err = dispatch.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM public.assozeta_go_job_ownership o JOIN public.assozeta_handoff_work w
  ON o.runtime=w.owner_runtime AND o.generation=w.owner_generation WHERE o.singleton=1 AND o.runtime='go' AND o.admission='open'
  AND w.task_id=$1 AND w.state='pending' FOR SHARE OF o)`, m.ID).Scan(&allowed)
	if err != nil {
		return err
	}
	if allowed {
		if err = b.Redis.pushPriority(ctx, queue, raw, envelope.Properties.Priority); err != nil {
			return err
		}
	}
	return dispatch.Commit()
}
