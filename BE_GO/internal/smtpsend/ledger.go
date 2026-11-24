package smtpsend

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/handoff"
)

// WithLedgerAttempt records each SMTP DATA effect against the running attempt.
func WithLedgerAttempt(ctx context.Context, db *sql.DB, task, attempt string, retries int64) context.Context {
	sequence := 0
	return WithEffectIntent(ctx, func(ctx context.Context) (func(bool) error, error) {
		key := fmt.Sprintf("%s:%d:smtp:%d", task, retries, sequence)
		sequence++
		var outcome string
		err := db.QueryRowContext(ctx, "SELECT public.assozeta_handoff_effect_intent($1,$2,'smtp')", attempt, key).Scan(&outcome)
		if err != nil || outcome == "confirmed" {
			return nil, err
		}
		if outcome != "recorded" {
			return nil, errors.New("SMTP effect needs reconciliation")
		}
		return func(accepted bool) error {
			if !accepted {
				_, err := db.ExecContext(ctx, "DELETE FROM public.assozeta_handoff_effects WHERE effect_key=$1 AND attempt_id=$2 AND state='intent'", key, attempt)
				return err
			}
			handoff.Barrier("effect:accepted", task)
			_, err := db.ExecContext(ctx, `SELECT public.assozeta_handoff_effect_confirm($1,NULL)
  FROM (SELECT public.assozeta_handoff_fence($2)) fenced`, key, attempt)
			return err
		}, nil
	})
}
