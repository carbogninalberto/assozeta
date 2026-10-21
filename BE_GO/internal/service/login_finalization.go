package service

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type LoginFinalization struct {
	store domain.LoginFinalizationStore
	now   func() time.Time
}

func NewLoginFinalization(store domain.LoginFinalizationStore, now func() time.Time) *LoginFinalization {
	return &LoginFinalization{store: store, now: now}
}

// Finalize accepts a trusted, already admitted account ID after response
// construction. The caller must not deliver tokens/cookies until this succeeds.
// Earlier password, username and card-configuration transactions are separate.
func (s *LoginFinalization) Finalize(ctx context.Context, id string, audit domain.LoginAuditContext) error {
	for attempt := 0; attempt < 3; attempt++ {
		err := s.finalizeAttempt(ctx, id, audit)
		if !errors.Is(err, domain.ErrCredentialConflict) {
			return err
		}
	}
	return domain.ErrCredentialConflict
}
func (s *LoginFinalization) finalizeAttempt(ctx context.Context, id string, audit domain.LoginAuditContext) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	tx, err := s.store.BeginLoginFinalization(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	state, err := tx.FinalizationForUpdate(ctx, id)
	if err != nil {
		return err
	}
	if state.ID != id || id == "" {
		return domain.ErrAccountNotFound
	}
	at := s.now().UTC()
	// Django makes a second clock read only when delete_on is present. Keep its
	// UTC date semantics, including a midnight between these two reads.
	clear := state.DeleteOn != nil && state.DeleteOn.Format(time.DateOnly) >= s.now().UTC().Format(time.DateOnly)
	if err = tx.SetLoginTime(ctx, id, at, clear); err != nil {
		return err
	}
	if clear {
		associations, err := tx.AuditAssociations(ctx, id, audit.ActorID)
		if err != nil {
			return err
		}
		if audit.ActorID == nil {
			audit.ActorEmail = nil
		}
		event := domain.LoginDeletionAudit{AccountID: id, ObjectRepresentation: "name: " + state.FirstName + " " + state.LastName, Before: state.DeleteOn.Format(time.DateOnly), Context: audit, At: at}
		association, path := associations[id], "user.sport_association"
		if association == "" && audit.ActorID != nil {
			association = associations[*audit.ActorID]
			path = "actor.sport_association"
		}
		if association != "" {
			event.AssociationID = &association
			event.ResolutionPath = &path
		}
		if err = tx.AppendLoginDeletionAudit(ctx, event); err != nil {
			return err
		}
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	return tx.Commit()
}
