package service

import (
	"context"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

type collaboratorReadFixture struct {
	calls  []string
	owners []uuid.UUID
	fail   string
}

func (f *collaboratorReadFixture) Collaborators(_ context.Context, owner uuid.UUID) ([]domain.CollaboratorRecord, error) {
	f.calls = append(f.calls, "users")
	f.owners = append(f.owners, owner)
	if f.fail == "users" {
		return nil, errors.New("database failed")
	}
	return []domain.CollaboratorRecord{}, nil
}
func (f *collaboratorReadFixture) CollaborationInvites(_ context.Context, owner uuid.UUID) ([]domain.CollaborationInviteRecord, error) {
	f.calls = append(f.calls, "invites")
	f.owners = append(f.owners, owner)
	return nil, nil
}
func (f *collaboratorReadFixture) Collaborator(_ context.Context, owner, id uuid.UUID) (*domain.CollaboratorRecord, error) {
	f.calls = append(f.calls, "detail")
	f.owners = append(f.owners, owner)
	return nil, nil
}
func TestCollaboratorResolvedScopeAndAdmission(t *testing.T) {
	owner := "a1000000-0000-4000-8000-000000610001"
	for _, role := range []int{1, 3} {
		f := &collaboratorReadFixture{}
		identity := domain.ResolvedIdentity{Actor: &domain.Account{ID: "must-not-be-used"}, ScopeUser: &domain.Account{ID: owner, Role: role}}
		s := NewCollaboratorList(f)
		data, err := s.Read(context.Background(), identity, nil)
		if err != nil || len(data.([]any)) != 0 || len(f.calls) != 2 {
			t.Fatal(data, err, f.calls)
		}
		for _, id := range f.owners {
			if id.String() != owner {
				t.Fatal("actor substituted for scope")
			}
		}
		empty := ""
		if _, err = s.Read(context.Background(), identity, &empty); err == nil || len(f.calls) != 2 {
			t.Fatal("empty detail became list")
		}
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		if _, err = s.Read(ctx, identity, nil); !errors.Is(err, context.Canceled) || len(f.calls) != 2 {
			t.Fatal("canceled request queried")
		}
	}
	for _, user := range []*domain.Account{nil, {ID: owner, Role: 2}} {
		f := &collaboratorReadFixture{}
		if _, err := NewCollaboratorList(f).Read(context.Background(), domain.ResolvedIdentity{ScopeUser: user}, nil); err == nil || len(f.calls) != 0 {
			t.Fatal("unauthorized read")
		}
	}
	f := &collaboratorReadFixture{fail: "users"}
	if _, err := NewCollaboratorList(f).Read(context.Background(), domain.ResolvedIdentity{ScopeUser: &domain.Account{ID: owner, Role: 1}}, nil); err == nil || len(f.calls) != 1 {
		t.Fatal("read failure continued")
	}
}
