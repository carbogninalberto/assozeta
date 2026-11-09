package service

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

const emailAssociation = "a1000000-0000-4000-8000-000000701001"

// inviteFake records the order of effects; the embedded interface panics on
// anything the invitation path must not call.
type inviteFake struct {
	domain.CollaboratorInviteTransaction
	events       *[]string
	associations []domain.EmailRenderAssociation
}

func (f *inviteFake) CreateInvite(context.Context, domain.CollaborationInvite) error {
	*f.events = append(*f.events, "create")
	return nil
}
func (f *inviteFake) AppendInviteAudit(context.Context, string, domain.CollaborationInviteAudit) error {
	*f.events = append(*f.events, "audit")
	return nil
}
func (f *inviteFake) ActiveAssociationOf(context.Context, string) (*string, error) { return nil, nil }
func (f *inviteFake) LiveAssociations(context.Context, string, *string) ([]domain.EmailRenderAssociation, error) {
	return f.associations, nil
}
func (f *inviteFake) Commit() error {
	*f.events = append(*f.events, "commit")
	return nil
}
func (f *inviteFake) Rollback() error { return nil }

type emailFakeStore struct {
	domain.EmailRenderStore
	tx *inviteFake
}

func (s emailFakeStore) BeginCollaboratorInvite(context.Context) (domain.CollaboratorInviteTransaction, error) {
	*s.tx.events = append(*s.tx.events, "begin")
	return s.tx, nil
}
func (s emailFakeStore) InstanceConfig(context.Context) (domain.EmailRenderInstanceConfig, error) {
	return domain.EmailRenderInstanceConfig{}, nil
}

func inviteFixture(associations ...domain.EmailRenderAssociation) (*EmailRenderRoutes, *orderedPublisher, *[]string) {
	events := []string{}
	publisher := &orderedPublisher{events: &events}
	routes := NewEmailRenderRoutes(emailFakeStore{tx: &inviteFake{events: &events, associations: associations}}, publisher,
		EmailRenderSettings{AppURL: "https://app.example.test", WhitelabelName: "W", DefaultFromEmail: "W <None>"}, time.Now)
	return routes, publisher, &events
}

func inviteIdentity() domain.ResolvedIdentity {
	owner := &domain.Account{ID: "a1000000-0000-4000-8000-000000700001", Role: domain.RoleAssociation}
	return domain.ResolvedIdentity{Actor: owner, Effective: owner, ScopeUser: owner}
}

func TestCollaboratorInvitePublishesAfterCommit(t *testing.T) {
	name := "ASD"
	routes, publisher, events := inviteFixture(domain.EmailRenderAssociation{ID: emailAssociation, Denomination: &name})
	err := routes.AddCollaborator(context.Background(), inviteIdentity(), updateBody("email", "x@example.test"), domain.LoginAuditContext{}, "it")
	if err != nil {
		t.Fatal(err)
	}
	if strings.Join(*events, ",") != "begin,create,audit,commit,publish" {
		t.Fatalf("order %v", *events)
	}
	task := publisher.tasks[0]
	if task.Name != "core.tasks.send_mail_async" || len(task.Kwargs) != 7 || task.Kwargs[6].Value != uuid.MustParse(emailAssociation) {
		t.Fatalf("task %+v", task)
	}
	if message := task.Kwargs[1].Value.(string); !strings.Contains(message, "ASD") || task.Kwargs[4].Value != message {
		t.Fatal("message")
	}
}

// Without a live association the invitation is committed and nothing is published.
func TestCollaboratorInviteWithoutAssociationKeepsRow(t *testing.T) {
	routes, publisher, events := inviteFixture()
	err := routes.AddCollaborator(context.Background(), inviteIdentity(), updateBody("email", "x@example.test"), domain.LoginAuditContext{}, "it")
	if !errors.Is(err, ErrEmailRenderServer) || len(publisher.tasks) != 0 || strings.Join(*events, ",") != "begin,create,audit,commit" {
		t.Fatalf("%v %v", err, *events)
	}
}

// Refused and failing inputs write nothing.
func TestCollaboratorInviteRefusalsWriteNothing(t *testing.T) {
	for name, body := range map[string]any{
		"exponent float": updateBody("email", "x@example.test", "collaborator_permissions", updateBody("k", json.Number("1e+16"))),
		"negative zero":  updateBody("email", "x@example.test", "collaborator_permissions", []any{json.Number("-0.0")}),
		"nul":            updateBody("email", "x@example.test", "collaborator_permissions", "a\x00"),
		"role":           updateBody("email", "x@example.test", "collaborator_role", "x"),
		"null body":      nil,
		"email list":     updateBody("email", []any{"@"}),
	} {
		routes, publisher, events := inviteFixture()
		err := routes.AddCollaborator(context.Background(), inviteIdentity(), body, domain.LoginAuditContext{}, "it")
		if err == nil || len(*events) != 0 || len(publisher.tasks) != 0 {
			t.Fatalf("%s: %v %v", name, err, *events)
		}
	}
	routes, _, _ := inviteFixture()
	var input *EmailInputError
	if err := routes.AddCollaborator(context.Background(), inviteIdentity(), updateBody("email", "bad"), domain.LoginAuditContext{}, "it"); !errors.As(err, &input) || input.Detail != "Email is not valid." {
		t.Fatal(err)
	}
	if !permissionsRoundTrip(json.Number("0.0001")) || permissionsRoundTrip(json.Number("0.00001")) || !permissionsRoundTrip(json.Number("1e15")) {
		t.Fatal("round trip bounds")
	}
}
