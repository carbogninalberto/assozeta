package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type twoFactorStoreTest struct {
	tx     twoFactorTxTest
	begins int
}

func (s *twoFactorStoreTest) BeginTwoFactorUpdate(context.Context) (domain.TwoFactorUpdateTransaction, error) {
	s.begins++
	return &s.tx, nil
}

type twoFactorTxTest struct {
	state          domain.TwoFactorState
	changed        bool
	enabled        bool
	secret         *string
	audit          *domain.TwoFactorAudit
	committed      bool
	fail           error
	commitFailures int
	rollbacks      int
	afterAudit     func()
}

func (t *twoFactorTxTest) TwoFactorForUpdate(context.Context, string) (domain.TwoFactorState, error) {
	return t.state, nil
}
func (t *twoFactorTxTest) SetTwoFactor(_ context.Context, _ string, enabled bool, secret *string) error {
	t.changed = true
	t.enabled = enabled
	t.secret = secret
	return nil
}
func (t *twoFactorTxTest) AuditAssociations(context.Context, string, *string) (map[string]string, error) {
	return map[string]string{"owner": "association"}, nil
}
func (t *twoFactorTxTest) AppendTwoFactorAudit(_ context.Context, event domain.TwoFactorAudit) error {
	t.audit = &event
	if t.afterAudit != nil {
		t.afterAudit()
	}
	return t.fail
}
func (t *twoFactorTxTest) Commit() error {
	if t.commitFailures > 0 {
		t.commitFailures--
		return domain.ErrCredentialConflict
	}
	t.committed = true
	return nil
}
func (t *twoFactorTxTest) Rollback() error { t.rollbacks++; return nil }

func TestTwoFactorUpdateValidationPrecedesPersistence(t *testing.T) {
	secret := "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
	identity := domain.ResolvedIdentity{Actor: &domain.Account{ID: "actor"}, Effective: &domain.Account{ID: "effective"}, ScopeUser: &domain.Account{ID: "owner"}}
	for _, tc := range []struct {
		name                string
		enable, secret, otp any
		invalid             bool
		enabled             bool
	}{
		{"enable", true, secret, "287082", false, true},
		{"truthy string", "false", secret, "287082", false, true},
		{"disable preserves secret", false, "", "", false, false},
		{"invalid secret before disable", false, "!invalid", "123456", true, false},
		{"short otp bypasses invalid secret", false, "!invalid", "short", false, false},
		{"bad OTP", true, secret, "287083", true, false},
		{"numeric OTP", false, secret, 123456, true, false},
		{"missing secret", true, nil, nil, true, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			store := &twoFactorStoreTest{tx: twoFactorTxTest{state: domain.TwoFactorState{ID: "owner", Enabled: !tc.enabled}}}
			service := NewTwoFactorUpdates(store, func() time.Time { return time.Unix(59, 0) })
			enabled, err := service.Update(context.Background(), identity, dto.TwoFactorUpdateRequest{Enable: tc.enable, Secret: tc.secret, OTP: tc.otp}, domain.LoginAuditContext{})
			if (err != nil) != tc.invalid {
				t.Fatal("validation boundary differs", err)
			}
			if tc.invalid {
				if store.begins != 0 {
					t.Fatal("invalid request acquired write transaction")
				}
				return
			}
			if enabled != tc.enabled || !store.tx.committed || !store.tx.changed || store.tx.audit == nil {
				t.Fatal("mutation not completed")
			}
			if *store.tx.audit.AssociationID != "association" || store.tx.audit.AccountID != "owner" {
				t.Fatal("scope/audit target differs")
			}
			if !tc.enabled && store.tx.secret != nil {
				t.Fatal("disable erased or replaced secret")
			}
		})
	}
	store := &twoFactorStoreTest{}
	service := NewTwoFactorUpdates(store, time.Now)
	if _, err := service.Update(context.Background(), domain.ResolvedIdentity{}, dto.TwoFactorUpdateRequest{}, domain.LoginAuditContext{}); err == nil || store.begins != 0 {
		t.Fatal("untrusted identity admitted")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := service.Update(ctx, identity, dto.TwoFactorUpdateRequest{}, domain.LoginAuditContext{}); !errors.Is(err, context.Canceled) || store.begins != 0 {
		t.Fatal("cancelled call admitted")
	}
}

func TestTwoFactorUpdateAuditFailurePreventsCommit(t *testing.T) {
	store := &twoFactorStoreTest{tx: twoFactorTxTest{state: domain.TwoFactorState{ID: "owner", Enabled: true}, fail: errors.New("audit denied")}}
	identity := domain.ResolvedIdentity{Actor: &domain.Account{}, Effective: &domain.Account{}, ScopeUser: &domain.Account{ID: "owner"}}
	_, err := NewTwoFactorUpdates(store, time.Now).Update(context.Background(), identity, dto.TwoFactorUpdateRequest{Enable: false, Secret: "", OTP: ""}, domain.LoginAuditContext{})
	if err == nil || store.tx.committed {
		t.Fatal("audit failure committed user change")
	}
}

func TestTwoFactorUpdateRetriesAreBoundedAndCancelledAuditRollsBack(t *testing.T) {
	identity := domain.ResolvedIdentity{Actor: &domain.Account{ID: "actor"}, Effective: &domain.Account{ID: "effective"}, ScopeUser: &domain.Account{ID: "owner"}}
	for _, failures := range []int{2, 3} {
		store := &twoFactorStoreTest{tx: twoFactorTxTest{state: domain.TwoFactorState{ID: "owner", Enabled: true}, commitFailures: failures}}
		_, err := NewTwoFactorUpdates(store, time.Now).Update(context.Background(), identity, dto.TwoFactorUpdateRequest{}, domain.LoginAuditContext{})
		if store.begins != 3 || store.tx.rollbacks != 3 || store.tx.committed != (failures == 2) || errors.Is(err, domain.ErrCredentialConflict) != (failures == 3) {
			t.Fatal("retry or rollback bound differs", err)
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	store := &twoFactorStoreTest{tx: twoFactorTxTest{state: domain.TwoFactorState{ID: "owner", Enabled: true}, afterAudit: cancel}}
	_, err := NewTwoFactorUpdates(store, time.Now).Update(ctx, identity, dto.TwoFactorUpdateRequest{}, domain.LoginAuditContext{})
	if !errors.Is(err, context.Canceled) || store.tx.committed || store.tx.rollbacks != 1 {
		t.Fatal("cancelled audited mutation committed", err)
	}
}
