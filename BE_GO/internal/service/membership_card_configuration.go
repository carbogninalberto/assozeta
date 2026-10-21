package service

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
)

type MembershipCardConfigurations struct {
	store domain.MembershipCardConfigurationStore
	now   func() time.Time
}

func NewMembershipCardConfigurations(store domain.MembershipCardConfigurationStore, now func() time.Time) *MembershipCardConfigurations {
	return &MembershipCardConfigurations{store: store, now: now}
}

// ForLogin takes the already admitted account and the association selected by
// response construction. The repository rechecks both owner and association
// under a row lock; no caller-supplied tenant ID grants access by itself.
func (s *MembershipCardConfigurations) ForLogin(ctx context.Context, account domain.Account, association string, audit domain.LoginAuditContext) (*dto.MembershipCardConfiguration, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if account.Superuser || (account.Role != domain.RoleAssociation && account.Role != domain.RoleCollaborator) {
		return nil, nil
	}
	owner := account.ID
	if account.Role == domain.RoleCollaborator {
		if account.ConnectedUserID == nil {
			return nil, domain.ErrAssociationNotFound
		}
		owner = *account.ConnectedUserID
	}
	for attempt := 0; attempt < 3; attempt++ {
		result, err := s.ensure(ctx, owner, association, audit)
		if !errors.Is(err, domain.ErrCardConfigurationConflict) {
			return result, err
		}
	}
	return nil, domain.ErrCardConfigurationConflict
}
func (s *MembershipCardConfigurations) ensure(ctx context.Context, owner, association string, audit domain.LoginAuditContext) (*dto.MembershipCardConfiguration, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	tx, err := s.store.BeginCardConfiguration(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	if err = tx.LockAssociation(ctx, owner, association); err != nil {
		return nil, err
	}
	config, err := tx.FirstConfiguration(ctx, association)
	if err != nil {
		return nil, err
	}
	if config == nil {
		id := uuid.NewString()
		config = &domain.MembershipCardConfiguration{ID: id, AssociationID: association, CustomizedTemplate: json.RawMessage("null")}
		if audit.ActorID == nil {
			audit.ActorEmail = nil
		}
		event := domain.MembershipCardConfigurationAudit{ID: id, AssociationID: association, Context: audit, At: s.now(), Changes: map[string][2]string{
			"sport_association_membership_card_configuration_id": {"None", id},
			"sport_association":     {"None", association},
			"emit_only_on_approval": {"None", "False"},
			"customized_template":   {"None", "null"},
		}}
		if err = tx.CreateDefault(ctx, id, association); err != nil {
			return nil, err
		}
		if err = tx.AppendConfigurationAudit(ctx, event); err != nil {
			return nil, err
		}
	}
	if config.AssociationID != association {
		return nil, domain.ErrAssociationNotFound
	}
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return &dto.MembershipCardConfiguration{EmitOnlyOnApproval: config.EmitOnlyOnApproval, CustomizedTemplate: config.CustomizedTemplate}, nil
}
