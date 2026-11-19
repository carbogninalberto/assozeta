package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type outgoingTestStore struct {
	row domain.OutgoingMessage
}

func (s *outgoingTestStore) BeginMessageTemplates(context.Context) (domain.MessageTemplateTransaction, error) {
	return &outgoingTestTx{store: s}, nil
}

func (s *outgoingTestStore) FinishOutgoingMessage(_ context.Context, _, _, status string, _ time.Time) error {
	s.row.Status = status
	return nil
}

type outgoingTestTx struct {
	domain.MessageTemplateTransaction
	store *outgoingTestStore
}

func (*outgoingTestTx) Association(context.Context, string, *string, bool) (string, string, error) {
	return "association", "ASD Aurora", nil
}

func (*outgoingTestTx) Recipient(context.Context, string, string, *string) (domain.MessageRecipient, error) {
	return domain.MessageRecipient{Phone: "+393331234567"}, nil
}

func (t *outgoingTestTx) LockOutgoingMessage(context.Context, string, string, string) (domain.OutgoingMessage, error) {
	return t.store.row, nil
}

func (t *outgoingTestTx) ClaimOutgoingMessage(_ context.Context, _ string, row domain.OutgoingMessage, _ time.Time) error {
	t.store.row = row
	return nil
}

func (*outgoingTestTx) Commit() error   { return nil }
func (*outgoingTestTx) Rollback() error { return nil }

func TestMessageTemplateLocaleSelection(t *testing.T) {
	rows := []domain.MessageTemplate{
		{ID: "it", Channel: "email", Locale: "it", IsDefault: true},
		{ID: "en", Channel: "email", Locale: "en", IsDefault: true},
		{ID: "gb", Channel: "email", Locale: "en-gb", IsDefault: true},
		{ID: "whatsapp", Channel: "whatsapp", Locale: "en-gb", IsDefault: true},
	}
	for _, tc := range []struct{ channel, locale, id, want string }{
		{"email", "en-gb", "", "gb"}, {"email", "en-us", "", "en"},
		{"email", "de", "", "it"}, {"whatsapp", "en-gb", "", "whatsapp"}, {"email", "it", "gb", "gb"},
	} {
		row, err := chooseMessageTemplate(rows, tc.channel, tc.locale, tc.id)
		if err != nil || row.ID != tc.want {
			t.Errorf("%+v: row=%+v, err=%v", tc, row, err)
		}
	}
	if _, err := chooseMessageTemplate(rows, "email", "en-gb", "whatsapp"); !errors.Is(err, domain.ErrMessageTemplateNotFound) {
		t.Fatal(err)
	}
}

func TestOutgoingMessageUsesApprovedTextAndClaimsOnce(t *testing.T) {
	ctx := context.Background()
	account := &domain.Account{ID: "actor", Role: domain.RoleAssociation, Active: true}
	identity := domain.ResolvedIdentity{Actor: account, Effective: account, ScopeUser: account}
	for _, tc := range []struct {
		action, want     string
		direct, accepted bool
	}{
		{"send", "submitted", true, true}, {"send", "failed", true, false},
		{"send", "skipped", false, false}, {"discard", "discarded", true, true}, {"open_whatsapp", "manual", false, false},
	} {
		t.Run(tc.want, func(t *testing.T) {
			store := &outgoingTestStore{row: domain.OutgoingMessage{ID: "preview", Channel: "whatsapp", Recipient: "+393331234567", Body: "Rendered", Status: "preview"}}
			approved := "  Edited text & emoji 😊\n "
			calls := 0
			delivery := map[string]MessageDelivery{}
			if tc.direct {
				delivery["whatsapp"] = func(_ context.Context, _ string, row domain.OutgoingMessage, _ domain.LoginAuditContext) (bool, error) {
					calls++
					if row.Body != approved || store.row.Status != "sending" {
						t.Fatalf("delivery before claim, or different text: %+v", row)
					}
					return tc.accepted, nil
				}
			}
			svc := NewMessageTemplates(store, delivery, time.Now)
			data := domain.RequestObject{{Name: "action", Value: tc.action}, {Name: "body", Value: approved}}
			for i := 0; i < 2; i++ {
				result, err := svc.Action(ctx, identity, "preview", "", data, domain.LoginAuditContext{})
				if err != nil || result.Status != tc.want || result.Body != approved ||
					(result.WhatsAppURL != "") != (tc.want == "manual") ||
					(result.Warning != "") != (tc.want != "submitted") {
					t.Fatalf("result=%+v, error=%v", result, err)
				}
			}
			wantCalls := 0
			if tc.action == "send" && tc.direct {
				wantCalls = 1
			}
			if calls != wantCalls {
				t.Fatalf("%d calls, want %d", calls, wantCalls)
			}
			data[1].Value = "different text"
			if _, err := svc.Action(ctx, identity, "preview", "", data, domain.LoginAuditContext{}); !errors.Is(err, ErrOutgoingMessageUsed) {
				t.Fatal(err)
			}
		})
	}
}
