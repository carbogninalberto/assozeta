package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type SocialConfiguration struct{ db *sqlx.DB }

func NewSocialConfiguration(db *sqlx.DB) *SocialConfiguration { return &SocialConfiguration{db: db} }

func (r *SocialConfiguration) SocialConfiguration(ctx context.Context) (*domain.SocialConfiguration, error) {
	var row domain.SocialConfiguration
	// Django .first() selects the smallest primary key even if historical data
	// bypassed the model's singleton save check. No cache spans operations.
	err := r.db.GetContext(ctx, &row, `SELECT self_hosted,integration_settings,display_settings FROM instance_instanceconfiguration ORDER BY id LIMIT 1`)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}
