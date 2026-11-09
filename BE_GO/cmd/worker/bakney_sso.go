package main

import (
	"context"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/jmoiron/sqlx"
)

type bakneySSOStore struct{ repository *repository.BakneySSO }

func (s bakneySSOStore) Begin(ctx context.Context) (service.BakneySSOTransaction, error) {
	tx, err := s.repository.Begin(ctx)
	if err != nil {
		return nil, err
	}
	return tx, nil
}

func (s bakneySSOStore) BeginJob(ctx context.Context, job domain.Job) (service.BakneySSOTransaction, error) {
	tx, err := s.repository.BeginJob(ctx, job)
	if err != nil {
		return nil, err
	}
	return tx, nil
}

// composeBakneyMaintenance builds maintain_bakney_pairings with the protocol
// settings Django's worker reads; it never issues local sessions.
func composeBakneyMaintenance(cfg config.Config, db *sqlx.DB) (*service.BakneySSO, func()) {
	settings := service.BakneySSOSettings{Debug: cfg.Debug, DeploymentMode: cfg.InstanceAdmin.DeploymentMode, AppURL: cfg.InstanceAdmin.AppURL,
		APIBase: cfg.InstanceAdmin.BakneySSOAPIBase, UIOrigin: cfg.InstanceAdmin.BakneySSOUIOrigin, SecretKeys: []string{cfg.SessionSecret}}
	authority := service.NewBakneyAuthorityClient()
	return service.NewBakneySSO(bakneySSOStore{repository.NewBakneySSO(db)}, settings, authority, nil, nil, nil, nil, time.Now),
		authority.CloseIdleConnections
}
