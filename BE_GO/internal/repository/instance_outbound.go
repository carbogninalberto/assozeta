package repository

import (
	"context"
	"encoding/json"
)

// ClaimLease is operation_limits.claim_operation's transaction:
// select_for_update(nowait=True).first(), then save(update_fields=
// ['diagnostic_results']) with the value apply returns (nil: no claim).
// Every database error is Django's DatabaseError (no claim); an error from
// apply is returned as is.
func (r *InstanceAdmin) ClaimLease(ctx context.Context, apply func(json.RawMessage) (json.RawMessage, error)) (bool, error) {
	tx, err := r.db.BeginTxx(ctx, nil)
	if err != nil {
		return false, nil
	}
	defer tx.Rollback()
	var row struct {
		ID      int64  `db:"id"`
		Results []byte `db:"diagnostic_results"`
	}
	if err = tx.GetContext(ctx, &row, `SELECT id, diagnostic_results FROM instance_instanceconfiguration ORDER BY id LIMIT 1 FOR UPDATE NOWAIT`); err != nil {
		return false, nil
	}
	results, err := apply(row.Results)
	if err != nil {
		return false, err
	}
	if results == nil {
		return false, nil
	}
	if _, err = tx.ExecContext(ctx, `UPDATE instance_instanceconfiguration SET diagnostic_results=$1 WHERE id=$2`, string(results), row.ID); err != nil {
		return false, nil
	}
	if err = tx.Commit(); err != nil {
		return false, nil
	}
	return true, nil
}
