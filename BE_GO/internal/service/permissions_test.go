package service

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestCollaboratorPolicyContracts(t *testing.T) {
	data, err := os.ReadFile("../../../TESTS/fixtures/collaborator-policy.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name, Path, Method, Detail string
		Permissions                []string
		Role                       int
		Collaborator               bool
	}
	if err := json.Unmarshal(data, &cases); err != nil {
		t.Fatal(err)
	}
	if len(cases) == 0 {
		t.Fatal("no policy contracts")
	}
	policy := NewPermissions()
	for _, tc := range cases {
		t.Run(tc.Name, func(t *testing.T) {
			subject := domain.PermissionSubject{Collaborator: tc.Collaborator, CollaboratorRole: tc.Role, Grants: tc.Permissions}
			err := policy.CheckCollaborator(context.Background(), subject, tc.Method, tc.Path)
			detail := ""
			if err != nil {
				var denied *domain.PermissionDenied
				if !errors.As(err, &denied) {
					t.Fatalf("unexpected error: %v", err)
				}
				detail = denied.Detail
			}
			if detail != tc.Detail {
				t.Fatalf("denial = %q, want %q", detail, tc.Detail)
			}
		})
	}
}

func TestCollaboratorPolicyRespectsCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	err := NewPermissions().CheckCollaborator(ctx, domain.PermissionSubject{}, "GET", "/profile/info")
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("cancellation: %v", err)
	}
}
