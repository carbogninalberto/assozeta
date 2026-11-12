package service

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type ImpersonationManagement struct {
	accounts  ActorAccounts
	directory domain.ImpersonationDirectory
	sessions  domain.ImpersonationSessionStore
	now       func() time.Time
	newID     func() string
}

func NewImpersonationManagement(accounts ActorAccounts, directory domain.ImpersonationDirectory, sessions domain.ImpersonationSessionStore, now func() time.Time, newID func() string) *ImpersonationManagement {
	return &ImpersonationManagement{accounts, directory, sessions, now, newID}
}

func (s *ImpersonationManagement) association(ctx context.Context, actor *domain.Account) (*domain.ImpersonationAssociation, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if actor == nil || actor.ID == "" {
		return nil, denial("Accesso non consentito.")
	}
	fresh, err := s.accounts.ByID(ctx, actor.ID)
	if errors.Is(err, domain.ErrAccountNotFound) || (err == nil && (!fresh.Active || fresh.Deleted)) {
		return nil, denial("Account non disponibile.")
	}
	if err != nil {
		return nil, err
	}
	if activeAdministrator(&fresh) {
		return nil, nil
	}
	if fresh.Role != domain.RoleAssociation {
		return nil, denial("Solo il titolare può impersonare utenti.")
	}
	association, err := s.directory.ImpersonationAssociation(ctx, fresh.ID)
	if errors.Is(err, domain.ErrAssociationNotFound) {
		return nil, denial("Associazione non disponibile.")
	}
	if err != nil {
		return nil, err
	}
	return &association, nil
}

// The boolean is CanManageImpersonation's permission return value. Explicit
// denials retain their own detail; the handler maps false to DRF's standard
// authenticated/anonymous permission response.
func (s *ImpersonationManagement) Admit(ctx context.Context, actor *domain.Account, administrative bool, method string) (bool, error) {
	if err := ctx.Err(); err != nil {
		return false, err
	}
	if administrative {
		return activeAdministrator(actor), nil
	}
	if method == "DELETE" {
		return actor != nil && actor.ID != "" && actor.Active && !actor.Deleted, nil
	}
	_, err := s.association(ctx, actor)
	return err == nil, err
}

func scopeAssociationID(scope *domain.ImpersonationAssociation) string {
	if scope == nil {
		return ""
	}
	return scope.ID
}

func (s *ImpersonationManagement) eligible(ctx context.Context, actor *domain.Account, scope *domain.ImpersonationAssociation, input any) (domain.ImpersonationPerson, error) {
	if input == nil {
		return domain.ImpersonationPerson{}, denial("Utente non disponibile per l’impersonificazione.")
	}
	var id string
	var err error
	if text, ok := input.(string); ok {
		// Management uses Django UUIDField's Python constructor grammar, already
		// characterized by the checkout parser. Global identity scope gates keep
		// their existing parser and are not loosened by this local conversion.
		parsed, parseErr := paymentInfoParseUUID(text)
		id, err = parsed.String(), parseErr
	} else {
		id, _, err = referenceUUID(input)
	}
	if err != nil {
		return domain.ImpersonationPerson{}, denial("Utente non disponibile per l’impersonificazione.")
	}
	person, err := s.directory.EligibleImpersonationPerson(ctx, actor.ID, scopeAssociationID(scope), id)
	if errors.Is(err, domain.ErrAccountNotFound) {
		return person, denial("Utente non disponibile per l’impersonificazione.")
	}
	return person, err
}

func managementSessionKey(value string) (string, error) {
	id, err := paymentInfoParseUUID(value)
	if err != nil {
		return "", denial("Sessione di impersonificazione non valida. Torna al tuo account.")
	}
	return id.String(), nil
}

func (s *ImpersonationManagement) Current(ctx context.Context, actor *domain.Account, previous string) (dto.ImpersonationCurrent, error) {
	scope, err := s.association(ctx, actor)
	if err != nil {
		return dto.ImpersonationCurrent{}, err
	}
	if previous == "" {
		return dto.ImpersonationCurrent{}, nil
	}
	key, err := managementSessionKey(previous)
	if err != nil {
		return dto.ImpersonationCurrent{}, err
	}
	record, err := s.sessions.Get(ctx, key)
	if errors.Is(err, domain.ErrImpersonationNotFound) || (err == nil && (record.ActorID != actor.ID || record.AssociationID != scopeAssociationID(scope))) {
		return dto.ImpersonationCurrent{}, denial("Sessione di impersonificazione scaduta o revocata. Torna al tuo account.")
	}
	if err != nil {
		return dto.ImpersonationCurrent{}, err
	}
	person, err := s.eligible(ctx, actor, scope, record.TargetID)
	if err != nil {
		return dto.ImpersonationCurrent{}, err
	}
	described, err := dto.DescribeImpersonationPerson(person, scope)
	return dto.ImpersonationCurrent{Target: &described}, err
}

func (s *ImpersonationManagement) Start(ctx context.Context, actor *domain.Account, body any, previous string, audit domain.LoginAuditContext) (dto.ImpersonationStarted, error) {
	var zero dto.ImpersonationStarted
	scope, err := s.association(ctx, actor)
	if err != nil {
		return zero, err
	}
	object, ok := body.(domain.RequestObject)
	if !ok || len(object) != 1 || object[0].Name != "target_user_id" {
		return zero, &domain.ImpersonationValidationError{Detail: "Seleziona un utente da impersonificare."}
	}
	person, err := s.eligible(ctx, actor, scope, object[0].Value)
	if err != nil {
		return zero, err
	}
	id := s.newID()
	if err = s.transition(ctx, actor, person, id, "started", scopeAssociationID(scope), audit); err != nil {
		return zero, err
	}
	if err = s.sessions.Put(ctx, id, domain.ImpersonationSession{ActorID: actor.ID, TargetID: person.ID, AssociationID: scopeAssociationID(scope)}); err != nil {
		return zero, err
	}
	if previous != "" {
		if err = s.End(ctx, actor, previous, audit); err != nil {
			// Preserve Django's explicit compensation ordering and error precedence.
			if cleanup := s.End(ctx, actor, id, audit); cleanup != nil {
				return zero, cleanup
			}
			return zero, err
		}
	}
	personDTO, err := dto.DescribeImpersonationPerson(person, scope)
	if err != nil {
		return zero, err
	}
	role := "administrator"
	if scope != nil {
		role = "association"
	}
	return dto.ImpersonationStarted{SessionID: id, Target: personDTO, ExpiresIn: 3600, ActorRole: role, Association: scope}, nil
}

func (s *ImpersonationManagement) End(ctx context.Context, actor *domain.Account, previous string, audit domain.LoginAuditContext) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if actor == nil || actor.ID == "" {
		return denial("Accesso non consentito.")
	}
	if previous == "" {
		return nil
	}
	key, err := managementSessionKey(previous)
	if err != nil {
		return err
	}
	record, err := s.sessions.Get(ctx, key)
	if errors.Is(err, domain.ErrImpersonationNotFound) {
		return nil
	}
	if err != nil {
		return err
	}
	if record.ActorID != actor.ID {
		return denial("Sessione di un altro account.")
	}
	person, err := s.directory.ImpersonationPersonIncludingDeleted(ctx, record.TargetID)
	if err != nil && !errors.Is(err, domain.ErrAccountNotFound) {
		return err
	}
	if err == nil {
		if err = s.transition(ctx, actor, person, previous, "ended", record.AssociationID, audit); err != nil {
			return err
		}
	}
	return s.sessions.Delete(ctx, key)
}

func (s *ImpersonationManagement) transition(ctx context.Context, actor *domain.Account, target domain.ImpersonationPerson, id, action, association string, audit domain.LoginAuditContext) error {
	var scope *string
	if association != "" {
		scope = &association
	}
	audit.ActorID = &actor.ID
	audit.ActorEmail = &actor.Email
	audit.AdditionalData = nil
	return s.directory.AppendImpersonationTransition(ctx, domain.ImpersonationTransition{Actor: *actor, Target: target, SessionID: id, Action: action, ScopeAssociationID: scope, Audit: audit, At: s.now()})
}
