package service

import (
	"context"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/manual"
)

type ManualToolContextReader interface {
	ManualToolContext(context.Context, string) (domain.ManualToolSnapshot, error)
}
type ManualToolExecutor interface {
	ExecuteTool(context.Context, manual.ToolContext, string, map[string]any) (map[string]any, error)
}
type ManualTools struct {
	context ManualToolContextReader
	source  ManualToolExecutor
}

func NewManualTools(contextReader ManualToolContextReader, source ManualToolExecutor) *ManualTools {
	return &ManualTools{context: contextReader, source: source}
}

// Execute receives identity and association from trusted transport admission.
// A zero identity is public knowledge access (e.g. stdio), never owner access.
// Impersonation admission/revocation belongs to that transport; the effective
// user, not the actor or resolved tenant owner, controls manual authority.
// Every valid call rereads instance/owner facts. Nothing grants business access.
func (s *ManualTools) Execute(ctx context.Context, identity domain.ResolvedIdentity, associationID, name string, arguments map[string]any) (map[string]any, error) {
	if ctx == nil {
		return nil, domain.ErrInvalidScopedRequest
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	var parameters []string
	switch name {
	case "search_manual":
		parameters = []string{"query", "limit"}
	case "get_manual_section", "get_manual_evidence":
		parameters = []string{"section_id"}
	case "get_manual_gaps":
		parameters = []string{"page"}
	default:
		return nil, manual.ErrUnknownTool
	}
	if name == "search_manual" {
		query, ok := arguments["query"].(string)
		query = strings.TrimFunc(query, func(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f })
		if !ok || query == "" || utf8.RuneCountInString(query) > 2000 {
			return map[string]any{"status": "invalid_query", "results": []any{}, "message": "Specifica una domanda sul manuale."}, nil
		}
	}
	userID := ""
	if identity.Actor != nil || identity.Effective != nil || identity.ScopeUser != nil {
		if err := manualReaderIdentity(ctx, identity); err != nil {
			return nil, err
		}
		if identity.Actor.ID == "" || identity.ScopeUser.ID == "" || identity.Actor.ID != identity.Effective.ID && !identity.Impersonating {
			return nil, domain.ErrInvalidScopedRequest
		}
		userID = identity.Effective.ID
	} else if identity.Impersonating || identity.Collaborator || identity.SessionID != "" || identity.ImpersonationAssociationID != "" {
		return nil, domain.ErrInvalidScopedRequest
	}
	snapshot, err := s.context.ManualToolContext(ctx, userID)
	if err != nil {
		return nil, err
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	caller := manual.ToolContext{Features: []string{}}
	if snapshot.SelfHosted {
		caller.Features = []string{"self_hosted"}
	}
	caller.Maintainer = snapshot.SelfHosted && userID != "" && snapshot.ActiveUserID == userID && snapshot.PrimaryOwnerID == userID && associationID != "" && snapshot.PrimaryAssociationID == associationID
	args := make(map[string]any, len(parameters))
	for _, key := range parameters {
		if value, present := arguments[key]; present {
			args[key] = value
		}
	}
	return s.source.ExecuteTool(ctx, caller, name, args)
}
