package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// paymentStateEdgesStore serves one locked payment and records what the
// approve and cancel routes wrote.
type paymentStateEdgesStore struct{ tx *paymentStateEdgesTx }

func (s *paymentStateEdgesStore) BeginPaymentState(context.Context) (domain.PaymentStateTransaction, error) {
	return s.tx, nil
}

type paymentStateEdgesTx struct {
	domain.PaymentStateTransaction
	row       domain.PaymentStateRow
	saves     int
	commits   int
	rollbacks int
	events    []domain.PaymentArchiveEvent
}

func (t *paymentStateEdgesTx) ScopedAssociation(context.Context, string, *string) (string, error) {
	return "association", nil
}

func (t *paymentStateEdgesTx) PaymentState(context.Context, domain.PaymentStateQuery) (*domain.PaymentStateRow, error) {
	row := t.row
	return &row, nil
}

func (t *paymentStateEdgesTx) SavePaymentState(context.Context, domain.PaymentStateSave) error {
	t.saves++
	return nil
}

func (t *paymentStateEdgesTx) AppendPaymentAudit(_ context.Context, event domain.PaymentArchiveEvent) error {
	t.events = append(t.events, event)
	return nil
}

func (t *paymentStateEdgesTx) RenderPayment(_ context.Context, id string, _ *string) (domain.PaymentStateRender, error) {
	return domain.PaymentStateRender{Payment: domain.CourseSubscriptionPayment{InvoicePartyPayment: domain.InvoicePartyPayment{ID: id}}}, nil
}

func (t *paymentStateEdgesTx) Commit() error   { t.commits++; return nil }
func (t *paymentStateEdgesTx) Rollback() error { t.rollbacks++; return nil }

func paymentStateEdgesIdentity() domain.ResolvedIdentity {
	account := &domain.Account{ID: "user", Email: "user@example.test", Role: domain.RoleAssociation}
	return domain.ResolvedIdentity{Actor: account, ScopeUser: account}
}

const paymentStateEdgesID = "a1000000-0000-4000-8000-000000985001"

func paymentStateEdgesRow(depth int, paid bool) domain.PaymentStateRow {
	category, description := "category", "Quota"
	return domain.PaymentStateRow{PaymentSaveRow: domain.PaymentSaveRow{ID: paymentStateEdgesID, AssociationID: "association",
		Subject: 0, CategoryID: &category, CategoryName: &category, Description: &description, Course: jsonEdgesArray(depth)}, Paid: paid}
}

func paymentStateEdgesService(tx *paymentStateEdgesTx) *PaymentStates {
	return NewPaymentStates(&paymentStateEdgesStore{tx: tx}, func() time.Time {
		return time.Date(2026, 11, 17, 9, 0, 0, 0, time.UTC)
	})
}

// The approve and cancel bands: the save runs up to auditlog's refetch (Save),
// the index falls back past Index, and past the view's load (Load) the request
// is the 500 page. Both views make their paid or unpaid change only after the
// refetch parses, so a recursion error leaves nothing written (approve is
// atomic; cancel writes nothing before its save).
func TestPaymentStateJSONEdgeBands(t *testing.T) {
	for _, verb := range []string{"approve", "cancel"} {
		for _, test := range []struct {
			name     string
			depth    int
			recurses bool
			after    bool
			fallback bool
		}{
			{name: "index at", depth: paymentStateJSONEdges.Index},
			{name: "index past", depth: paymentStateJSONEdges.Index + 1, fallback: true},
			{name: "save at", depth: paymentStateJSONEdges.Save, fallback: true},
			{name: "save past", depth: paymentStateJSONEdges.Save + 1, recurses: true, after: true},
			{name: "load at", depth: paymentStateJSONEdges.Load, recurses: true, after: true},
			{name: "load past", depth: paymentStateJSONEdges.Load + 1, recurses: true},
		} {
			paid := verb == "cancel"
			tx := &paymentStateEdgesTx{row: paymentStateEdgesRow(test.depth, paid)}
			service := paymentStateEdgesService(tx)
			request := PaymentStateRequest{ID: paymentStateEdgesID}
			var result map[string]any
			var err error
			if paid {
				result, err = service.Cancel(context.Background(), paymentStateEdgesIdentity(), request)
			} else {
				result, err = service.Approve(context.Background(), paymentStateEdgesIdentity(), request, domain.RequestObject{})
			}
			if test.recurses {
				var recursion *PaymentStoredJSONRecursionError
				if !errors.As(err, &recursion) {
					t.Fatalf("%s %s: err=%v, want the recursion error", verb, test.name, err)
				}
				if recursion.AfterWrites != test.after {
					t.Errorf("%s %s: AfterWrites=%v want %v", verb, test.name, recursion.AfterWrites, test.after)
				}
				if !errors.Is(err, refusal.ErrAppState) || errors.Is(err, refusal.ErrNotPorted) {
					t.Errorf("%s %s: err=%v, want an application-state error", verb, test.name, err)
				}
				if tx.saves != 0 || len(tx.events) != 0 || tx.commits != 0 {
					t.Errorf("%s %s: saves=%d events=%d commits=%d, want no write", verb, test.name, tx.saves, len(tx.events), tx.commits)
				}
				if tx.rollbacks == 0 {
					t.Errorf("%s %s: the transaction must be rolled back", verb, test.name)
				}
				if result != nil {
					t.Errorf("%s %s: result=%v", verb, test.name, result)
				}
				continue
			}
			if err != nil {
				t.Fatalf("%s %s: err=%v", verb, test.name, err)
			}
			if tx.saves != 1 || tx.commits != 1 {
				t.Errorf("%s %s: saves=%d commits=%d, want one save and the commit", verb, test.name, tx.saves, tx.commits)
			}
			if len(tx.events) != 1 {
				t.Fatalf("%s %s: events=%d want 1", verb, test.name, len(tx.events))
			}
			if tx.events[0].IndexFallback != test.fallback {
				t.Errorf("%s %s: IndexFallback=%v want %v", verb, test.name, tx.events[0].IndexFallback, test.fallback)
			}
		}
	}
}

// A payment the view only reads (already paid) meets the recursion edge at its
// own load, before the response is serialized.
func TestPaymentStatePaidApproveJSONLoadEdge(t *testing.T) {
	tx := &paymentStateEdgesTx{row: paymentStateEdgesRow(paymentStateJSONEdges.Load+1, true)}
	service := paymentStateEdgesService(tx)
	_, err := service.Approve(context.Background(), paymentStateEdgesIdentity(), PaymentStateRequest{ID: paymentStateEdgesID},
		domain.RequestObject{})
	var recursion *PaymentStoredJSONRecursionError
	if !errors.As(err, &recursion) || recursion.AfterWrites || !errors.Is(err, refusal.ErrAppState) {
		t.Fatalf("err=%v, want the load's recursion error", err)
	}
	if tx.commits != 0 || tx.rollbacks == 0 {
		t.Fatalf("commits=%d rollbacks=%d, want a rollback", tx.commits, tx.rollbacks)
	}
}
