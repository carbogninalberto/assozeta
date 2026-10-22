package service

import (
	"context"
	"errors"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	passwordcrypto "github.com/carbogninalberto/assozeta/BE_GO/pkg/crypto"
)

var ErrTwoFactorOTP = errors.New("two-factor OTP validation failed")

type TwoFactorUpdates struct {
	store domain.TwoFactorUpdateStore
	now   func() time.Time
}

func NewTwoFactorUpdates(store domain.TwoFactorUpdateStore, now func() time.Time) *TwoFactorUpdates {
	return &TwoFactorUpdates{store, now}
}

func (s *TwoFactorUpdates) Update(ctx context.Context, identity domain.ResolvedIdentity, input dto.TwoFactorUpdateRequest, audit domain.LoginAuditContext) (bool, error) {
	if err := ctx.Err(); err != nil {
		return false, err
	}
	if identity.Actor == nil || identity.Effective == nil || identity.ScopeUser == nil || identity.ScopeUser.ID == "" {
		return false, domain.ErrInvalidScopedRequest
	}
	audit.ActorID = &identity.Actor.ID
	audit.ActorEmail = &identity.Actor.Email
	audit.AdditionalData = nil
	if identity.Impersonating {
		var association *string
		if identity.ImpersonationAssociationID != "" {
			value := identity.ImpersonationAssociationID
			association = &value
		}
		audit.AdditionalData = &domain.AuditImpersonation{SessionID: identity.SessionID, ActorID: identity.Actor.ID, TargetID: identity.Effective.ID, AssociationID: association}
	}
	enabled := truthyReference(input.Enable)
	secret, otp := input.Secret, input.OTP
	if !truthyReference(secret) {
		secret = ""
	}
	if !truthyReference(otp) {
		otp = ""
	}
	valid := false
	// Python tests OTP length and decodes the submitted secret even when disabling.
	if secret != "" && otp != "" {
		length := 0
		supplied := ""
		switch value := otp.(type) {
		case string:
			length = utf8.RuneCountInString(value)
			supplied = value
		case []any:
			length = len(value)
		case domain.RequestObject:
			length = len(value)
		case domain.RequestUpload:
			if value.Size == 6 {
				length = 6
				supplied = value.Name
			}
		default:
			return false, ErrOTPShape
		}
		if length == 6 {
			text, ok := secret.(string)
			if !ok {
				return false, passwordcrypto.ErrTOTPSecret
			}
			var err error
			valid, err = passwordcrypto.VerifyTOTP(text, supplied, s.now())
			if err != nil {
				return false, err
			}
		}
	}
	if enabled && !valid {
		return false, ErrTwoFactorOTP
	}
	var replacement *string
	if enabled {
		text, ok := secret.(string)
		if !ok {
			return false, passwordcrypto.ErrTOTPSecret
		}
		replacement = &text
	}
	for attempt := 0; attempt < 3; attempt++ {
		err := s.updateAttempt(ctx, identity.ScopeUser.ID, enabled, replacement, audit)
		if !errors.Is(err, domain.ErrCredentialConflict) {
			return enabled, err
		}
	}
	return false, domain.ErrCredentialConflict
}

func (s *TwoFactorUpdates) updateAttempt(ctx context.Context, id string, enabled bool, secret *string, audit domain.LoginAuditContext) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	tx, err := s.store.BeginTwoFactorUpdate(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	state, err := tx.TwoFactorForUpdate(ctx, id)
	if err != nil {
		return err
	}
	changes := map[string][2]string{}
	boolean := func(v bool) string {
		if v {
			return "True"
		}
		return "False"
	}
	if state.Enabled != enabled {
		changes["two_fa"] = [2]string{boolean(state.Enabled), boolean(enabled)}
	}
	if secret != nil && (state.Secret == nil || *state.Secret != *secret) {
		before := "None"
		if state.Secret != nil {
			before = *state.Secret
		}
		changes["two_fa_secret"] = [2]string{before, *secret}
	}
	if err = tx.SetTwoFactor(ctx, id, enabled, secret); err != nil {
		return err
	}
	if len(changes) > 0 {
		associations, err := tx.AuditAssociations(ctx, id, audit.ActorID)
		if err != nil {
			return err
		}
		if audit.ActorID == nil {
			audit.ActorEmail = nil
		}
		event := domain.TwoFactorAudit{AccountID: id, ObjectRepresentation: "name: " + state.FirstName + " " + state.LastName, Changes: changes, Context: audit, At: s.now()}
		association, path := associations[id], "user.sport_association"
		if association == "" && audit.ActorID != nil {
			association = associations[*audit.ActorID]
			path = "actor.sport_association"
		}
		if association != "" {
			event.AssociationID = &association
			event.ResolutionPath = &path
		}
		if err = tx.AppendTwoFactorAudit(ctx, event); err != nil {
			return err
		}
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	return tx.Commit()
}
