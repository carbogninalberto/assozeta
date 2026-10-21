package service

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"golang.org/x/text/cases"
	"golang.org/x/text/language"
)

var ErrLoginCredentials = errors.New("invalid credentials")
var ErrLoginNotFound = errors.New("invalid credential (not found)")

type LoginPreparation struct {
	store domain.LoginPreparationStore
	now   func() time.Time
}

func NewLoginPreparation(store domain.LoginPreparationStore, now func() time.Time) *LoginPreparation {
	return &LoginPreparation{store: store, now: now}
}

// Prepare follows successful credentials, and commits username normalization and
// its audit invariant before OTP. The returned challenge is not a session.
func (s *LoginPreparation) Prepare(ctx context.Context, id string, audit domain.LoginAuditContext) (domain.LoginChallenge, error) {
	for attempt := 0; attempt < 3; attempt++ {
		result, err := s.prepareAttempt(ctx, id, audit)
		if !errors.Is(err, domain.ErrCredentialConflict) {
			return result, err
		}
	}
	return domain.LoginChallenge{}, domain.ErrCredentialConflict
}
func (s *LoginPreparation) prepareAttempt(ctx context.Context, id string, audit domain.LoginAuditContext) (domain.LoginChallenge, error) {
	empty := domain.LoginChallenge{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	tx, err := s.store.BeginLoginPreparation(ctx)
	if err != nil {
		return empty, err
	}
	defer tx.Rollback()
	account, err := tx.AccountForUpdate(ctx, id)
	if err != nil {
		return empty, err
	}
	if !account.Active || account.Deleted {
		return empty, ErrLoginCredentials
	}
	normalized := cases.Upper(language.Und).String(account.Username)
	if normalized != account.Username {
		associations, err := tx.AuditAssociations(ctx, account.ID, audit.ActorID)
		if err != nil {
			return empty, err
		}
		if audit.ActorID == nil {
			audit.ActorEmail = nil
		}
		event := domain.UsernameAudit{AccountID: account.ID, ObjectRepresentation: "name: " + account.FirstName + " " + account.LastName, Before: account.Username, After: normalized, Context: audit, At: s.now()}
		association, path := associations[account.ID], "user.sport_association"
		if association == "" && audit.ActorID != nil {
			association = associations[*audit.ActorID]
			path = "actor.sport_association"
		}
		if association != "" {
			event.AssociationID = &association
			event.ResolutionPath = &path
		}
		if err = tx.SetUsername(ctx, account.ID, normalized); err != nil {
			return empty, err
		}
		if err = tx.AppendUsernameAudit(ctx, event); err != nil {
			return empty, err
		}
		account.Username = normalized
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if err := tx.Commit(); err != nil {
		return empty, err
	}
	account.Password = ""
	return account, nil
}
