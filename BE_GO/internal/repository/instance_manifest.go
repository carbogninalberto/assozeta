package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type InstanceManifest struct{ db sqlx.QueryerContext }

func NewInstanceManifest(db sqlx.QueryerContext) *InstanceManifest {
	return &InstanceManifest{db: db}
}

const instanceManifestSQL = `SELECT name,abbreviation,meta_description,logo_path,primary_color
 FROM instance_instanceconfiguration ORDER BY id LIMIT 1`

func (r *InstanceManifest) FirstManifestBranding(ctx context.Context) (*domain.InstanceManifestBranding, error) {
	var value domain.InstanceManifestBranding
	if err := sqlx.GetContext(ctx, r.db, &value, instanceManifestSQL); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, nil
		}
		return nil, err
	}
	return &value, nil
}
