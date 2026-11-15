package service

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/emailrender"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
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
		"nul":        updateBody("email", "x@example.test", "collaborator_permissions", "a\x00"),
		"role":       updateBody("email", "x@example.test", "collaborator_role", "x"),
		"null body":  nil,
		"email list": updateBody("email", []any{"@"}),
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
	for in, want := range map[string]string{"0.00001": "0.00001", "1e15": "1e15", "1e16": "10000000000000000", "-1.5e+20": "-150000000000000000000", "-0.0": "0.0", "7": "7"} {
		if got := permissionsLoaded(json.Number(in)); got != json.Number(want) {
			t.Fatalf("%s -> %v", in, got)
		}
	}
}

// nanPaymentStore is payment_request's read of a payment whose numeric(9,2)
// amount holds NaN (a legacy or imported row).
type nanPaymentStore struct{ domain.EmailRenderStore }

func (nanPaymentStore) InstanceConfig(context.Context) (domain.EmailRenderInstanceConfig, error) {
	return domain.EmailRenderInstanceConfig{}, nil
}

func (nanPaymentStore) Associations(context.Context, string, *string) ([]domain.EmailRenderAssociation, error) {
	return []domain.EmailRenderAssociation{{ID: emailAssociation}}, nil
}

func (nanPaymentStore) PaymentForRequest(context.Context, string, *string, *string) (*domain.EmailPaymentRequest, error) {
	association, first, last, email := emailAssociation, "Mario", "Rossi", "mario.rossi@example.test"
	return &domain.EmailPaymentRequest{ID: "a1000000-0000-4000-8000-000000699003", Amount: "NaN", Association: &association,
		HasAssociate: true, AssociateFirst: &first, AssociateLast: &last, AssociateEmail: &email}, nil
}

// formats.localize raises while formatting Decimal('NaN'), so Django answers
// the 500 page after rendering: Go returns that error, not a refusal.
func TestPaymentRequestNonFiniteAmountIsNotARefusal(t *testing.T) {
	events := []string{}
	publisher := &orderedPublisher{events: &events}
	routes := NewEmailRenderRoutes(nanPaymentStore{}, publisher,
		EmailRenderSettings{AppURL: "https://app.example.test", WhitelabelName: "W"}, time.Now)
	owner := &domain.Account{ID: "a1000000-0000-4000-8000-000000700001", Role: domain.RoleAssociation,
		Email: "owner@example.test"}
	identity := domain.ResolvedIdentity{Actor: owner, Effective: owner, ScopeUser: owner}
	err := routes.RequestPayment(context.Background(), identity, "a1000000-0000-4000-8000-000000699003", "", "it")
	if !errors.Is(err, emailrender.ErrNonFiniteDecimal) {
		t.Fatalf("error %v", err)
	}
	if _, refused := refusal.Reason(err); refused {
		t.Fatalf("refusal: %v", err)
	}
	if len(publisher.tasks) != 0 {
		t.Fatalf("published %d tasks", len(publisher.tasks))
	}
}

// nanInvoiceStore is invoice_send's read of an invoice whose numeric(9,2)
// fees hold NaN (a legacy or imported row).
type nanInvoiceStore struct{ domain.EmailRenderStore }

func (nanInvoiceStore) InstanceConfig(context.Context) (domain.EmailRenderInstanceConfig, error) {
	return domain.EmailRenderInstanceConfig{}, nil
}

func (nanInvoiceStore) Associations(context.Context, string, *string) ([]domain.EmailRenderAssociation, error) {
	return []domain.EmailRenderAssociation{{ID: emailAssociation}}, nil
}

func (nanInvoiceStore) InvoiceForSend(context.Context, string, *string, *string) (*domain.EmailInvoice, error) {
	token := "document-token"
	return &domain.EmailInvoice{ID: "a1000000-0000-4000-8000-000000699001", Association: emailAssociation,
		MembershipFee: "NaN", ActivityFee: "NaN", DocumentToken: &token, HasDocumentPDF: true}, nil
}

func (nanInvoiceStore) InvoicePayment(context.Context, string, *string, *string) (*domain.EmailInvoicePayment, error) {
	first, last, email := "Mario", "Rossi", "mario.rossi@example.test"
	paid := time.Date(2026, 3, 5, 10, 0, 0, 0, time.UTC)
	return &domain.EmailInvoicePayment{PaymentDate: &paid, HasAssociate: true, AssociateFirst: &first, AssociateLast: &last,
		AssociateEmail: &email}, nil
}

// floatformat returns str(float) for a non-finite value, so Django renders the
// receipt with "nan" and queues it.
func TestSendInvoiceNonFiniteFeesRenderNan(t *testing.T) {
	events := []string{}
	publisher := &orderedPublisher{events: &events}
	routes := NewEmailRenderRoutes(nanInvoiceStore{}, publisher,
		EmailRenderSettings{AppURL: "https://app.example.test", WhitelabelName: "W", DefaultTeamEmail: "team@example.test"}, time.Now)
	owner := &domain.Account{ID: "a1000000-0000-4000-8000-000000700001", Role: domain.RoleAssociation,
		Email: "owner@example.test"}
	identity := domain.ResolvedIdentity{Actor: owner, Effective: owner, ScopeUser: owner}
	if err := routes.SendInvoice(context.Background(), identity, "a1000000-0000-4000-8000-000000699001", "", "it"); err != nil {
		t.Fatal(err)
	}
	if len(publisher.tasks) != 1 {
		t.Fatalf("published %d tasks", len(publisher.tasks))
	}
	if message := publisher.tasks[0].Kwargs[1].Value.(string); !strings.Contains(message, "nan") {
		t.Fatal("receipt does not render nan")
	}
}
