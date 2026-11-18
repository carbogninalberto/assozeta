package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// paymentArchiveEdgesStore serves one locked payment and records what the
// archive route wrote.
type paymentArchiveEdgesStore struct{ tx *paymentArchiveEdgesTx }

func (s *paymentArchiveEdgesStore) BeginPaymentArchive(context.Context) (domain.PaymentArchiveTransaction, error) {
	return s.tx, nil
}

type paymentArchiveEdgesTx struct {
	domain.PaymentArchiveTransaction
	row       domain.PaymentSaveRow
	saves     int
	commits   int
	rollbacks int
	events    []domain.PaymentArchiveEvent
}

func (t *paymentArchiveEdgesTx) OwnerAssociation(context.Context, string) (string, error) {
	return "association", nil
}

func (t *paymentArchiveEdgesTx) PaymentForArchive(context.Context, string, string, *string) (domain.PaymentSaveRow, error) {
	return t.row, nil
}

func (t *paymentArchiveEdgesTx) DefaultPaymentCategory(context.Context, *string) (*string, *string, error) {
	return nil, nil, errors.New("default category lookup unexpected")
}

func (t *paymentArchiveEdgesTx) PaymentLinkedName(context.Context, string) (domain.PaymentLinkedName, error) {
	return domain.PaymentLinkedName{}, nil
}

func (t *paymentArchiveEdgesTx) SavePaymentArchive(context.Context, string, bool, *string, *string) error {
	t.saves++
	return nil
}

func (t *paymentArchiveEdgesTx) AppendPaymentAudit(_ context.Context, event domain.PaymentArchiveEvent) error {
	t.events = append(t.events, event)
	return nil
}

func (t *paymentArchiveEdgesTx) Commit() error   { t.commits++; return nil }
func (t *paymentArchiveEdgesTx) Rollback() error { t.rollbacks++; return nil }

func paymentArchiveEdgesIdentity() domain.ResolvedIdentity {
	account := &domain.Account{ID: "user", Email: "user@example.test", Role: domain.RoleAssociation}
	return domain.ResolvedIdentity{Actor: account, ScopeUser: account}
}

func paymentArchiveEdgesRow(depth int) domain.PaymentSaveRow {
	category, description := "category", "Quota"
	return domain.PaymentSaveRow{ID: "a1000000-0000-4000-8000-000000940001", AssociationID: "association", Subject: 0,
		CategoryID: &category, CategoryName: &category, Description: &description, Course: jsonEdgesArray(depth)}
}

// One depth at and one past each measured edge of payment_archive: the save
// runs up to the auditlog refetch (Save), the index entry falls back past
// Index, and a value past the view's own load (Load) is the 500 page before
// anything is written. The view writes nothing before its save, so both
// recursion bands leave the database unchanged.
func TestPaymentArchiveJSONEdgeBands(t *testing.T) {
	for _, test := range []struct {
		name     string
		depth    int
		recurses bool
		after    bool
		fallback bool
	}{
		{name: "index at", depth: paymentArchiveJSONEdges.Index, fallback: false},
		{name: "index past", depth: paymentArchiveJSONEdges.Index + 1, fallback: true},
		{name: "save at", depth: paymentArchiveJSONEdges.Save, fallback: true},
		{name: "save past", depth: paymentArchiveJSONEdges.Save + 1, recurses: true, after: true},
		{name: "load at", depth: paymentArchiveJSONEdges.Load, recurses: true, after: true},
		{name: "load past", depth: paymentArchiveJSONEdges.Load + 1, recurses: true},
	} {
		tx := &paymentArchiveEdgesTx{row: paymentArchiveEdgesRow(test.depth)}
		archive := NewPaymentArchives(&paymentArchiveEdgesStore{tx: tx}, func() time.Time {
			return time.Date(2026, 11, 17, 9, 0, 0, 0, time.UTC)
		})
		message, err := archive.Archive(context.Background(), paymentArchiveEdgesIdentity(), "a1000000-0000-4000-8000-000000940001", "", domain.LoginAuditContext{})
		if test.recurses {
			var recursion *PaymentStoredJSONRecursionError
			if !errors.As(err, &recursion) {
				t.Fatalf("%s: err=%v, want the recursion error", test.name, err)
			}
			if recursion.AfterWrites != test.after {
				t.Errorf("%s: AfterWrites=%v want %v", test.name, recursion.AfterWrites, test.after)
			}
			if !errors.Is(err, refusal.ErrAppState) || errors.Is(err, refusal.ErrNotPorted) {
				t.Errorf("%s: err=%v, want an application-state error", test.name, err)
			}
			if tx.saves != 0 || len(tx.events) != 0 || tx.commits != 0 {
				t.Errorf("%s: saves=%d events=%d commits=%d, want no write", test.name, tx.saves, len(tx.events), tx.commits)
			}
			if tx.rollbacks == 0 {
				t.Errorf("%s: the transaction must be rolled back", test.name)
			}
			if message != "" {
				t.Errorf("%s: message=%q", test.name, message)
			}
			continue
		}
		if err != nil {
			t.Fatalf("%s: err=%v", test.name, err)
		}
		if message != "Payment archived." {
			t.Errorf("%s: message=%q", test.name, message)
		}
		if tx.saves != 1 || tx.commits != 1 {
			t.Errorf("%s: saves=%d commits=%d, want one save and the commit", test.name, tx.saves, tx.commits)
		}
		if len(tx.events) != 1 {
			t.Fatalf("%s: events=%d want 1", test.name, len(tx.events))
		}
		if tx.events[0].IndexFallback != test.fallback {
			t.Errorf("%s: IndexFallback=%v want %v", test.name, tx.events[0].IndexFallback, test.fallback)
		}
	}
}
