package main

import (
	"context"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/middleware"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/jwt"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

type bakneySSOStore struct{ repository *repository.BakneySSO }

func (s bakneySSOStore) Begin(ctx context.Context) (service.BakneySSOTransaction, error) {
	tx, err := s.repository.Begin(ctx)
	if err != nil {
		return nil, err
	}
	return tx, nil
}

type bakneyAdministrators struct{ bootstrap *service.InstanceBootstrap }

// Allowed is PairingAdministrator: is_instance_administrator on the actor.
func (a bakneyAdministrators) Allowed(ctx context.Context, actor domain.Account) (bool, error) {
	access, err := a.bootstrap.Access(ctx, actor)
	if err != nil {
		return false, err
	}
	return access.IsAdministrator, nil
}

// composeBakneySSO builds the Bakney pairing/SSO views (BE/instance/sso):
// the protocol settings Django reads, the authority client, the local JWT
// login response (JWTTokenService) and the DRF throttle cache.
func composeBakneySSO(cfg config.Config, db *sqlx.DB, cache *redis.Client, tokens *jwt.Manager, accounts *repository.Accounts,
	identities *service.IdentityResolver, bootstrap *service.InstanceBootstrap, images domain.LoginImageProcessor) (*handler.BakneySSOHandler, func(), error) {
	throttle, err := repository.NewDRFThrottleCache(cache)
	if err != nil {
		return nil, nil, err
	}
	settings := service.BakneySSOSettings{Debug: cfg.Debug, DeploymentMode: cfg.InstanceAdmin.DeploymentMode, AppURL: cfg.InstanceAdmin.AppURL,
		APIBase: cfg.InstanceAdmin.BakneySSOAPIBase, UIOrigin: cfg.InstanceAdmin.BakneySSOUIOrigin, SecretKeys: []string{cfg.SessionSecret}}
	responses := service.NewLoginResponses(service.NewLoginProfiles(repository.NewLoginProfiles(db), time.Now),
		service.NewAssociationProfiles(repository.NewAssociationProfiles(db), time.Now), repository.NewLoginResponseSettings(db),
		service.NewMembershipCardConfigurations(repository.NewMembershipCardConfigurations(db), time.Now), images,
		domain.RegistrationChoices{Family: cfg.FamilyRegistrationEnabled, Federation: cfg.FederationRegistrationEnabled, Multiple: cfg.MultipleRegistrationEnabled})
	authority := service.NewBakneyAuthorityClient()
	actors := service.NewActorAuthentication(accounts, tokens)
	sso := service.NewBakneySSO(bakneySSOStore{repository.NewBakneySSO(db)}, settings, authority, accounts, tokens, responses, actors, time.Now)
	return handler.NewBakneySSOHandler(sso, actors, identities, bakneyAdministrators{bootstrap}, throttle, middleware.SessionAuditContext, time.Now),
		authority.CloseIdleConnections, nil
}
