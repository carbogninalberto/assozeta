package service

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type profileTemplateStoreTest struct {
	tx       profileTemplateTxTest
	begins   int
	beginErr error
}

func (s *profileTemplateStoreTest) BeginProfileTemplate(context.Context) (domain.ProfileTemplateTransaction, error) {
	s.begins++
	if s.beginErr != nil {
		return nil, s.beginErr
	}
	return &s.tx, nil
}

type profileTemplateTxTest struct {
	association    *domain.ProfileTemplateAssociation
	associationErr error
	audits         []domain.ProfileMiscAudit
	columns        []domain.ProfileTemplateColumn
	committed      bool
	rollbacks      int
}

func (t *profileTemplateTxTest) Association(context.Context, string, string) (*domain.ProfileTemplateAssociation, error) {
	return t.association, t.associationErr
}
func (t *profileTemplateTxTest) AppendAudit(_ context.Context, event domain.ProfileMiscAudit) error {
	t.audits = append(t.audits, event)
	return nil
}
func (t *profileTemplateTxTest) Save(_ context.Context, _ string, columns []domain.ProfileTemplateColumn) error {
	t.columns = append(t.columns, columns...)
	return nil
}
func (t *profileTemplateTxTest) Commit() error {
	t.committed = true
	return nil
}
func (t *profileTemplateTxTest) Rollback() error {
	t.rollbacks++
	return nil
}

func profileTemplateServiceIdentity() domain.ResolvedIdentity {
	owner := &domain.Account{ID: "owner", Role: domain.RoleAssociation, Email: "owner@example.test"}
	return domain.ResolvedIdentity{Actor: owner, Effective: owner, ScopeUser: owner}
}

func profileTemplateServicePayload(changes ...domain.RequestField) domain.RequestObject {
	data := domain.RequestObject{}
	for _, key := range []string{"additional_sections", "regulation", "demand", "show_demand_to_athletes", "show_demand_to_members",
		"show_demand_to_both", "show_regulation_to_athletes", "show_regulation_to_members", "show_regulation_to_both", "logo",
		"subscription_fee", "multiple_subscription_fee", "subscription_fee_plans", "membership_fee", "multiple_membership_fee",
		"membership_fee_plans", "enable_quotes_management", "enabled_for", "additional_fields", "stripe_available_methods",
		"invoice_template", "subscription_template", "extra_text_invoices"} {
		data = append(data, domain.RequestField{Name: key})
	}
	for _, change := range changes {
		data = dto.PythonSet(data, change.Name, change.Value)
	}
	return domain.RequestObject{{Name: "sport_association", Value: data}}
}

// TestProfileTemplateRejectedTextStopsBeforeWrites checks that a rejected text
// leaves the association untouched: the lookup runs, the transaction rolls
// back, and nothing is saved or audited.
func TestProfileTemplateRejectedTextStopsBeforeWrites(t *testing.T) {
	const (
		longMessage = "Il testo è troppo lungo (massimo 500000 caratteri)."
		deepMessage = "Il testo contiene troppi livelli annidati (massimo 32)."
	)
	for _, tc := range []struct {
		name        string
		sections    any
		field       domain.RequestField
		wantField   string
		wantMessage string
	}{
		{"regulation-too-deep", nil, domain.RequestField{Name: "regulation", Value: strings.Repeat("<b>", 33) + "x"},
			"regulation", deepMessage},
		{"demand-too-long", nil, domain.RequestField{Name: "demand", Value: strings.Repeat("a", 500001)},
			"demand", longMessage},
		{"section-before-regulation", []any{domain.RequestObject{{Name: "name", Value: "A"},
			{Name: "text", Value: strings.Repeat("<b>", 33) + "x"}}},
			domain.RequestField{Name: "regulation", Value: strings.Repeat("a", 500001)},
			"additional_sections", deepMessage},
	} {
		t.Run(tc.name, func(t *testing.T) {
			store := &profileTemplateStoreTest{tx: profileTemplateTxTest{association: &domain.ProfileTemplateAssociation{ID: "association"}}}
			template := NewProfileTemplate(store, nil, nil, nil, domain.RegistrationChoices{}, nil)
			changes := []domain.RequestField{tc.field}
			if tc.sections != nil {
				changes = append(changes, domain.RequestField{Name: "additional_sections", Value: tc.sections})
			}
			_, err := template.Update(context.Background(), profileTemplateServiceIdentity(),
				profileTemplateServicePayload(changes...), domain.LoginAuditContext{})
			var rejection *dto.ProfileTemplateTextRejected
			if !errors.As(err, &rejection) || rejection.Field != tc.wantField || rejection.Message != tc.wantMessage {
				t.Fatalf("%v", err)
			}
			if store.begins != 1 || store.tx.rollbacks != 1 {
				t.Fatalf("begins %d rollbacks %d", store.begins, store.tx.rollbacks)
			}
			if store.tx.committed || len(store.tx.audits) != 0 || len(store.tx.columns) != 0 {
				t.Fatalf("wrote on a rejected text: %v", store.tx)
			}
		})
	}
}

// TestProfileTemplateRejectedTextNeedsTheAssociation: the view's
// SportAssociation lookup runs before the section texts, so a missing
// association stays the 500 page even when a text would be rejected.
func TestProfileTemplateRejectedTextNeedsTheAssociation(t *testing.T) {
	lookupErr := errors.New("synthetic lookup failure")
	for _, tc := range []struct {
		name string
		tx   profileTemplateTxTest
		want error
	}{
		{"missing", profileTemplateTxTest{}, ErrProfileTemplateServer},
		{"lookup-error", profileTemplateTxTest{associationErr: lookupErr}, lookupErr},
	} {
		t.Run(tc.name, func(t *testing.T) {
			store := &profileTemplateStoreTest{tx: tc.tx}
			template := NewProfileTemplate(store, nil, nil, nil, domain.RegistrationChoices{}, nil)
			_, err := template.Update(context.Background(), profileTemplateServiceIdentity(),
				profileTemplateServicePayload(domain.RequestField{Name: "regulation", Value: strings.Repeat("<b>", 33) + "x"}),
				domain.LoginAuditContext{})
			var rejection *dto.ProfileTemplateTextRejected
			if errors.As(err, &rejection) || !errors.Is(err, tc.want) {
				t.Fatalf("%v", err)
			}
			if store.tx.committed || len(store.tx.audits) != 0 || len(store.tx.columns) != 0 {
				t.Fatalf("wrote without an association: %v", store.tx)
			}
		})
	}
}
