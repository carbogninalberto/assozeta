package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type cardStoreStub struct {
	begins   int
	existing *domain.MembershipCardConfiguration
	conflict bool
	txs      []*cardTxStub
}
type cardTxStub struct {
	store                      *cardStoreStub
	event                      domain.MembershipCardConfigurationAudit
	owner, association         string
	created, committed, rolled bool
}

func (s *cardStoreStub) BeginCardConfiguration(context.Context) (domain.MembershipCardConfigurationTransaction, error) {
	s.begins++
	tx := &cardTxStub{store: s}
	s.txs = append(s.txs, tx)
	return tx, nil
}
func (t *cardTxStub) LockAssociation(_ context.Context, owner, association string) error {
	t.owner, t.association = owner, association
	return nil
}
func (t *cardTxStub) FirstConfiguration(context.Context, string) (*domain.MembershipCardConfiguration, error) {
	return t.store.existing, nil
}
func (t *cardTxStub) CreateDefault(context.Context, string, string) error {
	t.created = true
	return nil
}
func (t *cardTxStub) AppendConfigurationAudit(_ context.Context, event domain.MembershipCardConfigurationAudit) error {
	t.event = event
	return nil
}
func (t *cardTxStub) Commit() error {
	if t.store.conflict {
		return domain.ErrCardConfigurationConflict
	}
	t.committed = true
	return nil
}
func (t *cardTxStub) Rollback() error { t.rolled = true; return nil }

func TestMembershipCardRoleOwnerAndAudit(t *testing.T) {
	owner := "owner"
	email := "must-not-be-attributed"
	store := &cardStoreStub{}
	result, err := NewMembershipCardConfigurations(store, time.Now).ForLogin(context.Background(), domain.Account{ID: "collaborator", Role: 3, ConnectedUserID: &owner}, "association", domain.LoginAuditContext{ActorEmail: &email})
	if err != nil || result == nil || result.EmitOnlyOnApproval || string(result.CustomizedTemplate) != "null" {
		t.Fatal("default configuration differs")
	}
	tx := store.txs[0]
	if tx.owner != owner || !tx.created || !tx.committed || tx.event.Context.ActorEmail != nil || len(tx.event.Changes) != 4 {
		t.Fatal("wrong owner/audit attribution or missing atomic effects")
	}
	for _, account := range []domain.Account{{Role: 2}, {Role: 1, Superuser: true}, {Role: 3, Superuser: true}} {
		if result, err := NewMembershipCardConfigurations(store, time.Now).ForLogin(context.Background(), account, "ignored", domain.LoginAuditContext{}); result != nil || err != nil {
			t.Fatal("unexpected configuration augmentation")
		}
	}
	if store.begins != 1 {
		t.Fatal("administrator/athlete touched database")
	}
}
func TestMembershipCardConflictDoesNotReturnUncommittedConfiguration(t *testing.T) {
	store := &cardStoreStub{conflict: true}
	result, err := NewMembershipCardConfigurations(store, time.Now).ForLogin(context.Background(), domain.Account{ID: "owner", Role: 1}, "association", domain.LoginAuditContext{})
	if result != nil || !errors.Is(err, domain.ErrCardConfigurationConflict) || store.begins != 3 {
		t.Fatal("conflict retry bound violated")
	}
	for _, tx := range store.txs {
		if !tx.rolled || tx.committed {
			t.Fatal("failed transaction retained effects")
		}
	}
}
func TestMembershipCardForeignExistingRowRejected(t *testing.T) {
	store := &cardStoreStub{existing: &domain.MembershipCardConfiguration{ID: "config", AssociationID: "foreign"}}
	result, err := NewMembershipCardConfigurations(store, time.Now).ForLogin(context.Background(), domain.Account{ID: "owner", Role: 1}, "association", domain.LoginAuditContext{})
	if result != nil || !errors.Is(err, domain.ErrAssociationNotFound) || store.txs[0].committed {
		t.Fatal("foreign stored configuration admitted")
	}
}
