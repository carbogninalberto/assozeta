package repository

import (
	"context"
	"encoding/json"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// SetGoTasksEnabled is InstanceAdminView.put_go_tasks after the runner
// accepted the switch: the configuration UPDATE, then the UPDATE log entry
// when the switch changed (str(previous), str(wanted)).
func (r *InstanceBackups) SetGoTasksEnabled(ctx context.Context, change domain.InstanceGoTasksChange) error {
	var previous bool
	if err := r.db.GetContext(ctx, &previous, `SELECT go_tasks_enabled FROM instance_instanceconfiguration ORDER BY id LIMIT 1`); err != nil {
		return err
	}
	if _, err := r.db.ExecContext(ctx, `UPDATE instance_instanceconfiguration SET go_tasks_enabled=$1, updated_at=$2
 WHERE id=(SELECT id FROM instance_instanceconfiguration ORDER BY id LIMIT 1)`, change.Enabled, change.At); err != nil {
		return err
	}
	if previous == change.Enabled {
		return nil
	}
	target, err := r.LogTarget(ctx)
	if err != nil {
		return err
	}
	if target == nil {
		return errors.New("instance configuration missing")
	}
	values := []string{dto.PythonStr(previous), dto.PythonStr(change.Enabled)}
	changes, err := json.Marshal(map[string][]string{"go_tasks_enabled": values})
	if err != nil {
		return err
	}
	additional, err := json.Marshal(map[string]string{"actor_id": change.ActorID})
	if err != nil {
		return err
	}
	return r.AppendBackupAction(ctx, InstanceBackupAction{Target: *target, ActorID: change.ActorID, Changes: changes,
		Additional: additional, Audit: change.Audit, At: change.At})
}
