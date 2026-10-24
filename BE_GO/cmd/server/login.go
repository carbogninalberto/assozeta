package main

import (
	"net/http"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/handler"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/middleware"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	passwordcrypto "github.com/carbogninalberto/assozeta/BE_GO/pkg/crypto"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/jwt"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/social"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

// composeLogin constructs login providers and credential admission pools. The
// composition root supplies the shared image pool used by all image-bearing routes.
func composeLogin(cfg config.Config, db *sqlx.DB, cache *redis.Client, tokens *jwt.Manager, accounts *repository.Accounts, identities *service.IdentityResolver, images domain.LoginImageProcessor) (*handler.LoginHandler, *service.LoginRate, func(), error) {
	counter, err := repository.NewDjangoLoginRateCache(cache)
	if err != nil {
		return nil, nil, nil, err
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.MaxConnsPerHost = 8
	requestsTransport := social.NewRequestsTransport(transport, cfg.ProviderCABundle)
	client := &http.Client{Transport: requestsTransport}
	providers := service.NewSocialProviders(
		service.NewSocialClientIDs(repository.NewSocialConfiguration(db), cfg.GoogleClientID, cfg.AppleClientID),
		social.NewGoogle(client, transport, time.Now, 4), social.NewApple(client, time.Now, 4))
	preparation := service.NewLoginPreparation(repository.NewLoginPreparations(db), time.Now)
	secondFactor := service.NewSecondFactor(time.Now)
	passwords := service.NewPasswordAdmission(service.NewPasswordLogin(repository.NewPasswordLogins(db), passwordcrypto.NewPasswordVerifier(passwordcrypto.DefaultPasswordLimits()), 2), preparation, secondFactor)
	socialAdmission := service.NewSocialAdmission(service.NewSocialLogin(accounts, providers, 4), preparation, secondFactor)
	responses := service.NewLoginResponses(service.NewLoginProfiles(repository.NewLoginProfiles(db), time.Now),
		service.NewAssociationProfiles(repository.NewAssociationProfiles(db), time.Now), repository.NewLoginResponseSettings(db),
		service.NewMembershipCardConfigurations(repository.NewMembershipCardConfigurations(db), time.Now), images,
		domain.RegistrationChoices{Family: cfg.FamilyRegistrationEnabled, Federation: cfg.FederationRegistrationEnabled, Multiple: cfg.MultipleRegistrationEnabled})
	sessions := service.NewLoginSessions(passwords, socialAdmission, tokens, responses, service.NewLoginFinalization(repository.NewLoginFinalizations(db), time.Now))
	return handler.NewLoginHandler(sessions, service.NewActorAuthentication(accounts, tokens), identities, middleware.SessionAuditContext),
		service.NewLoginRate(counter, time.Now), func() {
			requestsTransport.CloseIdleConnections()
			transport.CloseIdleConnections()
		}, nil
}
