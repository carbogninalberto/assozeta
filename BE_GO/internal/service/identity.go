package service

import (
	"context"
	"encoding/json"
	"errors"
	"regexp"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

type IdentityRelations interface {
	AssociationOwnedBy(context.Context, string) (string, error)
	EligibleTarget(context.Context, string, string, string) (domain.Account, error)
	PrimaryExportOwner(context.Context) (domain.Account, error)
}
type IdentitySessions interface {
	Get(context.Context, string) (domain.ImpersonationSession, error)
}
type IdentityResolver struct {
	accounts    ActorAccounts
	relations   IdentityRelations
	sessions    IdentitySessions
	permissions *Permissions
}

func NewIdentityResolver(accounts ActorAccounts, relations IdentityRelations, sessions IdentitySessions, permissions *Permissions) *IdentityResolver {
	return &IdentityResolver{accounts: accounts, relations: relations, sessions: sessions, permissions: permissions}
}

var adminUpdatePath = regexp.MustCompile(`^sport-associations/[0-9a-fA-F-]{36}/admin-update$`)

func activeAdministrator(account *domain.Account) bool {
	return account != nil && account.Active && !account.Deleted && account.Superuser
}
func cleanIdentityAccount(account *domain.Account) *domain.Account {
	if account == nil {
		return nil
	}
	copy := *account
	copy.Password = ""
	return &copy
}
func denial(detail string) error { return &domain.PermissionDenied{Detail: detail} }

// Resolve selects identities and enforces the collaborator route gate. It does
// not replace endpoint/resource authorization or Django enforce_scoped_request.
// In particular, an association-bound result MUST constrain all referenced and
// returned resources and undergo the scoped operation gate before side effects.
func (s *IdentityResolver) Resolve(ctx context.Context, actor *domain.Account, request domain.IdentityRequest) (domain.ResolvedIdentity, error) {
	var empty domain.ResolvedIdentity
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	result := domain.ResolvedIdentity{Actor: actor, Effective: actor, ScopeUser: actor}
	path := strings.TrimPrefix(strings.Trim(request.Path, "/"), "api/")
	administration := strings.HasPrefix(path, "instance/") || strings.HasPrefix(path, "administration/")
	control := path == "association/impersonation" || path == "association/impersonation/users"
	if administration && !activeAdministrator(actor) && request.SessionID != "" {
		return empty, denial("Esci dall’impersonificazione per accedere all’amministrazione.")
	}
	if !administration && !control && (request.SessionID != "" || request.TargetID != "") {
		target, association, err := s.resolveTarget(ctx, actor, request.SessionID, request.TargetID)
		if err != nil {
			var denied *domain.PermissionDenied
			if errors.As(err, &denied) {
				return empty, &domain.PermissionDenied{Detail: denied.Detail, ImpersonationInvalid: true}
			}
			return empty, err
		}
		result.Effective = target
		result.ScopeUser = target
		result.Impersonating = true
		result.SessionID = request.SessionID
		result.ImpersonationAssociationID = association
	}
	// Full request admission must insert enforce_scoped_request's operation and
	// nested-resource checks here, before collaborator-owner mapping and grants,
	// to preserve Django's denial ordering. This identity-only selector has no
	// public handler until that stage is implemented and verified.
	target := result.Effective
	if target != nil && target.Role == domain.RoleCollaborator && !administration && !control {
		if target.ConnectedUserID == nil {
			return empty, denial("Associazione del collaboratore non disponibile.")
		}
		owner, err := s.accounts.ByID(ctx, *target.ConnectedUserID)
		if errors.Is(err, domain.ErrAccountNotFound) || (err == nil && (!owner.Active || owner.Deleted)) {
			return empty, denial("Associazione del collaboratore non disponibile.")
		}
		if err != nil {
			return empty, err
		}
		result.ScopeUser = &owner
		result.Collaborator = true
	}
	if activeAdministrator(actor) && !result.Impersonating && strings.HasPrefix(path, "association/export/") {
		owner, err := s.relations.PrimaryExportOwner(ctx)
		if errors.Is(err, domain.ErrAssociationNotFound) {
			return empty, denial("Nessuna associazione principale configurata per l’export.")
		}
		if err != nil {
			return empty, err
		}
		result.ScopeUser = &owner
	}
	manual := request.Method == "GET" && (path == "manuale/sections" || strings.HasPrefix(path, "manuale/assets/"))
	allowed := path == "profile/info" || path == "sport-associations/list" || manual || adminUpdatePath.MatchString(path) || strings.HasPrefix(path, "association/export/") || strings.HasPrefix(path, "oauth2/") || strings.HasPrefix(path, "notifications/")
	if activeAdministrator(actor) && !result.Impersonating && !administration && !allowed {
		return empty, denial("Seleziona un utente da impersonificare per accedere a questa sezione.")
	}
	if result.Collaborator {
		subject := domain.PermissionSubject{Collaborator: true, CollaboratorRole: target.CollaboratorRole}
		// Exemptions/FULL and unknown-route denial precede inspection of stored JSON,
		// matching the existing registry. Owner/administrator grants are never used.
		preliminary := s.permissions.CheckCollaborator(ctx, subject, request.Method, request.Path)
		if errors.Is(preliminary, context.Canceled) || errors.Is(preliminary, context.DeadlineExceeded) {
			return empty, preliminary
		}
		if preliminary != nil && requiredPermission(path, request.Method) != "" {
			grants, err := persistedGrants(target.Permissions)
			if err != nil {
				return empty, err
			}
			subject.Grants = grants
		}
		if err := s.permissions.CheckCollaborator(ctx, subject, request.Method, request.Path); err != nil {
			return empty, err
		}
	}
	result.Actor = cleanIdentityAccount(result.Actor)
	result.Effective = cleanIdentityAccount(result.Effective)
	result.ScopeUser = cleanIdentityAccount(result.ScopeUser)
	return result, nil
}

func (s *IdentityResolver) resolveTarget(ctx context.Context, actor *domain.Account, sessionID, targetID string) (*domain.Account, string, error) {
	if actor == nil {
		return nil, "", denial("Accesso non consentito.")
	}
	fresh, err := s.accounts.ByID(ctx, actor.ID)
	if errors.Is(err, domain.ErrAccountNotFound) || (err == nil && (!fresh.Active || fresh.Deleted)) {
		return nil, "", denial("Account non disponibile.")
	}
	if err != nil {
		return nil, "", err
	}
	association := ""
	if !activeAdministrator(&fresh) {
		if fresh.Role != domain.RoleAssociation {
			return nil, "", denial("Solo il titolare può impersonare utenti.")
		}
		association, err = s.relations.AssociationOwnedBy(ctx, fresh.ID)
		if errors.Is(err, domain.ErrAssociationNotFound) {
			return nil, "", denial("Associazione non disponibile.")
		}
		if err != nil {
			return nil, "", err
		}
	}
	record, err := s.sessions.Get(ctx, sessionID)
	if errors.Is(err, domain.ErrInvalidImpersonation) {
		return nil, "", denial("Sessione di impersonificazione non valida. Torna al tuo account.")
	}
	if errors.Is(err, domain.ErrImpersonationNotFound) || (err == nil && (record.ActorID != fresh.ID || record.AssociationID != association || (targetID != "" && record.TargetID != targetID))) {
		return nil, "", denial("Sessione di impersonificazione scaduta o revocata. Torna al tuo account.")
	}
	if err != nil {
		return nil, "", err
	}
	targetUUID, err := uuid.Parse(record.TargetID)
	if err != nil {
		return nil, "", denial("Utente non disponibile per l’impersonificazione.")
	}
	target, err := s.relations.EligibleTarget(ctx, fresh.ID, association, targetUUID.String())
	if errors.Is(err, domain.ErrAccountNotFound) {
		return nil, "", denial("Utente non disponibile per l’impersonificazione.")
	}
	if err != nil {
		return nil, "", err
	}
	return &target, association, nil
}

func persistedGrants(raw json.RawMessage) ([]string, error) {
	if len(raw) == 0 {
		return nil, nil
	}
	var value any
	if err := json.Unmarshal(raw, &value); err != nil {
		return nil, domain.ErrInvalidPermissionState
	}
	var result []string
	switch value := value.(type) {
	case nil:
		return nil, nil
	case []any:
		for _, entry := range value {
			switch entry := entry.(type) {
			case string:
				result = append(result, entry)
			case nil, bool, float64:
			case map[string]any, []any:
				return nil, domain.ErrInvalidPermissionState
			}
		}
	case map[string]any:
		for key := range value {
			result = append(result, key)
		}
	case string:
		for _, character := range value {
			result = append(result, string(character))
		}
	case bool:
		if value {
			return nil, domain.ErrInvalidPermissionState
		}
	case float64:
		if value != 0 {
			return nil, domain.ErrInvalidPermissionState
		}
	default:
		return nil, domain.ErrInvalidPermissionState
	}
	return result, nil
}
