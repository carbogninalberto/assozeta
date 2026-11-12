package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/jmoiron/sqlx"
)

// Ordinary manual context needs only the public self-hosted flag. Never load
// integration credentials or derive maintainer privileges from an actor's role.
type ManualFeatures struct{ db *sqlx.DB }

func NewManualFeatures(db *sqlx.DB) *ManualFeatures { return &ManualFeatures{db} }
func (r *ManualFeatures) ManualFeatures(ctx context.Context) ([]string, error) {
	var enabled bool
	err := r.db.GetContext(ctx, &enabled, `SELECT self_hosted FROM instance_instanceconfiguration ORDER BY id LIMIT 1`)
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return nil, err
	}
	if enabled {
		return []string{"self_hosted"}, nil
	}
	return []string{}, nil
}
