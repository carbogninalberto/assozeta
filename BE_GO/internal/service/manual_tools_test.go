package service

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/manual"
)

type toolContextFixture struct {
	calls    int
	user     string
	snapshot domain.ManualToolSnapshot
	err      error
	cancel   context.CancelFunc
}

func (f *toolContextFixture) ManualToolContext(ctx context.Context, user string) (domain.ManualToolSnapshot, error) {
	f.calls++
	f.user = user
	if f.cancel != nil {
		f.cancel()
	}
	return f.snapshot, f.err
}

type toolSourceFixture struct {
	calls  int
	caller manual.ToolContext
	args   map[string]any
}

func (f *toolSourceFixture) ExecuteTool(ctx context.Context, caller manual.ToolContext, name string, args map[string]any) (map[string]any, error) {
	f.calls++
	f.caller = caller
	f.args = args
	return map[string]any{"status": "fixture"}, nil
}

func TestManualToolsLiveAuthorityAndArgumentIsolation(t *testing.T) {
	identity := manualIdentity()
	r := &toolContextFixture{snapshot: domain.ManualToolSnapshot{SelfHosted: true, PrimaryAssociationID: "association", PrimaryOwnerID: "actor", ActiveUserID: "actor"}}
	source := &toolSourceFixture{}
	s := NewManualTools(r, source)
	args := map[string]any{"query": "fixture", "maintainer": true, "features": []any{"secret"}, "user_id": "forged", "sport_association_id": "forged"}
	if _, err := s.Execute(context.Background(), identity, "association", "search_manual", args); err != nil || !source.caller.Maintainer || r.user != "actor" {
		t.Fatal(source.caller, r.user, err)
	}
	if !reflect.DeepEqual(source.args, map[string]any{"query": "fixture"}) {
		t.Fatal("untrusted arguments crossed boundary", source.args)
	}
	r.snapshot.ActiveUserID = ""
	if _, err := s.Execute(context.Background(), identity, "association", "search_manual", args); err != nil || source.caller.Maintainer || r.calls != 2 {
		t.Fatal("stale account granted authority", source.caller, err)
	}
	r.snapshot.ActiveUserID = "actor"
	if _, err := s.Execute(context.Background(), identity, "foreign", "search_manual", args); err != nil || source.caller.Maintainer {
		t.Fatal("foreign association", err)
	}
	if _, err := s.Execute(context.Background(), domain.ResolvedIdentity{}, "association", "search_manual", args); err != nil || source.caller.Maintainer || r.user != "" {
		t.Fatal("public call elevated", err)
	}
	if !reflect.DeepEqual(source.caller.Features, []string{"self_hosted"}) {
		t.Fatal("public live feature lost")
	}
}

func TestManualToolsEffectiveIdentityNotActorOrScope(t *testing.T) {
	r := &toolContextFixture{snapshot: domain.ManualToolSnapshot{SelfHosted: true, PrimaryAssociationID: "association", PrimaryOwnerID: "owner", ActiveUserID: "collaborator"}}
	source := &toolSourceFixture{}
	s := NewManualTools(r, source)
	owner := &domain.Account{ID: "owner", Active: true}
	collaborator := &domain.Account{ID: "collaborator", Active: true}
	identity := domain.ResolvedIdentity{Actor: collaborator, Effective: collaborator, ScopeUser: owner, Collaborator: true}
	if _, err := s.Execute(context.Background(), identity, "association", "get_manual_evidence", nil); err != nil || source.caller.Maintainer || r.user != "collaborator" {
		t.Fatal("tenant owner substituted for caller", source.caller, err)
	}
	identity = domain.ResolvedIdentity{Actor: owner, Effective: collaborator, ScopeUser: owner, Impersonating: true}
	if _, err := s.Execute(context.Background(), identity, "association", "get_manual_evidence", nil); err != nil || source.caller.Maintainer {
		t.Fatal("actor substituted for effective identity", err)
	}
	// An already admitted impersonation uses its effective owner. This does
	// not prove the separate session authorization/revocation boundary.
	r.snapshot.ActiveUserID = "owner"
	identity = domain.ResolvedIdentity{Actor: collaborator, Effective: owner, ScopeUser: owner, Impersonating: true}
	if _, err := s.Execute(context.Background(), identity, "association", "get_manual_evidence", nil); err != nil || !source.caller.Maintainer {
		t.Fatal("trusted effective owner lost", err)
	}
}

func TestManualToolsCanceledContextReadDoesNotLoadSource(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	r := &toolContextFixture{cancel: cancel}
	source := &toolSourceFixture{}
	result, err := NewManualTools(r, source).Execute(ctx, manualIdentity(), "association", "get_manual_section", nil)
	if result != nil || !errors.Is(err, context.Canceled) || source.calls != 0 {
		t.Fatal(result, err, source.calls)
	}
}

func TestManualToolsAdmissionAndErrorsPrecedeSource(t *testing.T) {
	r := &toolContextFixture{}
	source := &toolSourceFixture{}
	s := NewManualTools(r, source)
	for _, query := range []any{nil, 12, "\x1c\u00a0", strings.Repeat("à", 2001)} {
		result, err := s.Execute(context.Background(), domain.ResolvedIdentity{}, "", "search_manual", map[string]any{"query": query})
		if err != nil || result["status"] != "invalid_query" || r.calls != 0 || source.calls != 0 {
			t.Fatal(result, err)
		}
	}
	if _, err := s.Execute(context.Background(), domain.ResolvedIdentity{}, "", "unknown", nil); !errors.Is(err, manual.ErrUnknownTool) || r.calls != 0 {
		t.Fatal("unknown tool queried", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := s.Execute(ctx, manualIdentity(), "association", "get_manual_section", nil); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
	if _, err := s.Execute(nil, manualIdentity(), "association", "get_manual_section", nil); !errors.Is(err, domain.ErrInvalidScopedRequest) {
		t.Fatal(err)
	}
	r.err = errors.New("database unavailable")
	if result, err := s.Execute(context.Background(), manualIdentity(), "association", "get_manual_section", nil); result != nil || !errors.Is(err, r.err) || source.calls != 0 {
		t.Fatal("database failure became evidence", result, err)
	}
}
