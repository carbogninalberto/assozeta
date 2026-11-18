package service

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// paymentSignEdgesStore serves one locked payment and records what the sign
// route wrote.
type paymentSignEdgesStore struct{ tx *paymentSignEdgesTx }

func (s *paymentSignEdgesStore) BeginPaymentMisc(context.Context) (domain.PaymentMiscTransaction, error) {
	return s.tx, nil
}

type paymentSignEdgesTx struct {
	domain.PaymentMiscTransaction
	row              domain.PaymentStateRow
	signatures       int
	signatureUpdates int
	saves            int
	commits          int
	rollbacks        int
	events           []domain.PaymentArchiveEvent
}

func (t *paymentSignEdgesTx) SignPayment(context.Context, string, *string, *string) (bool, *string, error) {
	association := "association"
	return true, &association, nil
}

func (t *paymentSignEdgesTx) ScopedAssociation(context.Context, string, *string) (string, error) {
	return "association", nil
}

func (t *paymentSignEdgesTx) ActorAssociation(context.Context, string) (*string, error) {
	association := "association"
	return &association, nil
}

func (t *paymentSignEdgesTx) PaymentState(context.Context, domain.PaymentStateQuery) (*domain.PaymentStateRow, error) {
	row := t.row
	return &row, nil
}

func (t *paymentSignEdgesTx) CreatePaymentSignature(context.Context, domain.PaymentSignatureSave) error {
	t.signatures++
	return nil
}

func (t *paymentSignEdgesTx) SetPaymentSignature(context.Context, string, string) (*string, error) {
	t.signatureUpdates++
	return nil, nil
}

func (t *paymentSignEdgesTx) SavePaymentState(context.Context, domain.PaymentStateSave) error {
	t.saves++
	return nil
}

func (t *paymentSignEdgesTx) AppendPaymentAudit(_ context.Context, event domain.PaymentArchiveEvent) error {
	t.events = append(t.events, event)
	return nil
}

func (t *paymentSignEdgesTx) Commit() error   { t.commits++; return nil }
func (t *paymentSignEdgesTx) Rollback() error { t.rollbacks++; return nil }

func paymentSignEdgesRow(depth int) domain.PaymentStateRow {
	category, description := "category", "Quota"
	return domain.PaymentStateRow{PaymentSaveRow: domain.PaymentSaveRow{ID: paymentStateEdgesID, AssociationID: "association",
		Subject: 0, CategoryID: &category, CategoryName: &category, Description: &description, Course: jsonEdgesArray(depth)}}
}

func paymentSignEdgesBody() domain.RequestObject {
	return domain.RequestObject{
		{Name: "payment_id", Value: paymentStateEdgesID},
		{Name: "signature", Value: domain.RequestObject{{Name: "there_is_signature", Value: true}, {Name: "data", Value: "firma"}}},
	}
}

// The view loads the payment before it looks at the signature payload: a deep
// row is the 500 page even when the payload would not store a signature.
func TestPaymentSignDeepPaymentWithoutSignature(t *testing.T) {
	for _, depth := range []int{paymentSignJSONEdges.Load, paymentSignJSONEdges.Load + 1} {
		tx := &paymentSignEdgesTx{row: paymentSignEdgesRow(depth)}
		sign := NewPaymentMisc(&paymentSignEdgesStore{tx: tx}, func() time.Time {
			return time.Date(2026, 11, 17, 9, 0, 0, 0, time.UTC)
		}, func() string { return "a1000000-0000-4000-8000-000000988999" }, "W")
		body := domain.RequestObject{
			{Name: "payment_id", Value: paymentStateEdgesID},
			{Name: "signature", Value: domain.RequestObject{{Name: "there_is_signature", Value: false}, {Name: "data", Value: "firma"}}},
		}
		result, err := sign.Sign(context.Background(), paymentStateEdgesIdentity(), PaymentMiscRequest{Body: body})
		if depth > paymentSignJSONEdges.Load {
			var recursion *PaymentStoredJSONRecursionError
			if !errors.As(err, &recursion) || recursion.AfterWrites {
				t.Fatalf("depth %d: err=%v, want the load's recursion error", depth, err)
			}
			if tx.signatures != 0 || tx.commits != 0 {
				t.Fatalf("depth %d: signatures=%d commits=%d, want nothing written", depth, tx.signatures, tx.commits)
			}
			continue
		}
		if err != nil {
			t.Fatalf("depth %d: err=%v", depth, err)
		}
		if result["status"] != "success" || tx.signatures != 0 || tx.commits != 0 {
			t.Fatalf("depth %d: result=%v signatures=%d commits=%d, want the plain answer", depth, result, tx.signatures, tx.commits)
		}
	}
}

// the index entry falls back past Index, and past the view's own load (Load)
// the request is the 500 page. The view is not in a transaction, so the
// Signature rows it wrote before payment.save() stay committed past Save and
// are rolled back with it past Load.
func TestPaymentSignJSONEdgeBands(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(http.StatusOK) }))
	defer server.Close()
	for _, test := range []struct {
		name      string
		depth     int
		recurses  bool
		after     bool
		fallback  bool
		committed int
	}{
		{name: "index at", depth: paymentSignJSONEdges.Index, committed: 1},
		{name: "index past", depth: paymentSignJSONEdges.Index + 1, fallback: true, committed: 1},
		{name: "save at", depth: paymentSignJSONEdges.Save, fallback: true, committed: 1},
		{name: "save past", depth: paymentSignJSONEdges.Save + 1, recurses: true, after: true, committed: 1},
		{name: "load at", depth: paymentSignJSONEdges.Load, recurses: true, after: true, committed: 1},
		{name: "load past", depth: paymentSignJSONEdges.Load + 1, recurses: true},
	} {
		tx := &paymentSignEdgesTx{row: paymentSignEdgesRow(test.depth)}
		sign := NewPaymentMisc(&paymentSignEdgesStore{tx: tx}, func() time.Time {
			return time.Date(2026, 11, 17, 9, 0, 0, 0, time.UTC)
		}, func() string { return "a1000000-0000-4000-8000-000000988999" }, "W").WithCompensationPrint(
			NewInvoiceSyncPrint(&invoiceSyncAdmission{}, server.URL))
		_, err := sign.Sign(context.Background(), paymentStateEdgesIdentity(), PaymentMiscRequest{Body: paymentSignEdgesBody()})
		wantSignatures := 1
		if test.depth > paymentSignJSONEdges.Load {
			// The view's own load raises before Signature.objects.create.
			wantSignatures = 0
		}
		if tx.signatures != wantSignatures {
			t.Fatalf("%s: signatures=%d want %d", test.name, tx.signatures, wantSignatures)
		}
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
			// The payment's signature column is written by the same save that
			// raised, so it is never set; the Signature rows stay only when the
			// request is not atomic (past Save).
			if tx.saves != 0 || tx.signatureUpdates != 0 || len(tx.events) != 0 {
				t.Errorf("%s: saves=%d signature updates=%d events=%d, want the payment untouched", test.name, tx.saves,
					tx.signatureUpdates, len(tx.events))
			}
			if tx.commits != test.committed {
				t.Errorf("%s: commits=%d want %d", test.name, tx.commits, test.committed)
			}
			continue
		}
		if err != nil {
			t.Fatalf("%s: err=%v", test.name, err)
		}
		if tx.commits != 1 || tx.saves != 1 || tx.signatureUpdates != 1 {
			t.Errorf("%s: commits=%d saves=%d signature updates=%d, want the committed save", test.name, tx.commits, tx.saves,
				tx.signatureUpdates)
		}
		if len(tx.events) != 1 {
			t.Fatalf("%s: events=%d want 1", test.name, len(tx.events))
		}
		if tx.events[0].IndexFallback != test.fallback {
			t.Errorf("%s: IndexFallback=%v want %v", test.name, tx.events[0].IndexFallback, test.fallback)
		}
	}
}
