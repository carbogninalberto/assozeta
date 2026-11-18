package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// invoiceCoreEdgesTx is invoice_delete's transaction: one invoice whose first
// payment carries the deep column, with the writes recorded.
type invoiceCoreEdgesTx struct {
	domain.InvoiceCoreTransaction
	depth  int
	events *[]string
	saved  *[]domain.InvoiceCorePaymentSave
}

func (f invoiceCoreEdgesTx) log(event string) { *f.events = append(*f.events, event) }

func (f invoiceCoreEdgesTx) ScopedAssociation(context.Context, string, *string) (string, error) {
	return "assoc", nil
}

func (f invoiceCoreEdgesTx) MatchingInvoices(context.Context, string, []string, *string, *string) ([]string, error) {
	return []string{"invoice"}, nil
}

func (f invoiceCoreEdgesTx) PaymentToUnlink(context.Context, string, *string, *string) (*domain.InvoiceCorePaymentSave, error) {
	category, name, description := "category", "Quote", "Quota"
	association := "assoc"
	return &domain.InvoiceCorePaymentSave{PaymentSaveRow: domain.PaymentSaveRow{ID: "payment", AssociationID: "assoc",
		CategoryID: &category, CategoryName: &name, Description: &description, Course: jsonEdgesArray(f.depth)},
		Paid: true, Association: &association}, nil
}

func (f invoiceCoreEdgesTx) UnlinkPayment(context.Context, domain.InvoiceCorePaymentSave) error {
	f.log("payment-save")
	return nil
}

func (f invoiceCoreEdgesTx) AppendPaymentSave(_ context.Context, row domain.InvoiceCorePaymentSave, _ map[string][2]string,
	_ domain.InvoiceCoreDeleteContext) error {
	*f.saved = append(*f.saved, row)
	f.log("payment-audit")
	return nil
}

func (f invoiceCoreEdgesTx) DeleteInvoices(context.Context, []string, domain.InvoiceCoreDeleteContext) error {
	f.log("invoice-delete")
	return nil
}

func (f invoiceCoreEdgesTx) Commit() error {
	f.log("commit")
	return nil
}

func (f invoiceCoreEdgesTx) Rollback() error {
	f.log("rollback")
	return nil
}

type invoiceCoreEdgesStore struct {
	domain.InvoiceCoreStore
	tx invoiceCoreEdgesTx
}

func (s invoiceCoreEdgesStore) BeginInvoiceCore(context.Context) (domain.InvoiceCoreTransaction, error) {
	return s.tx, nil
}

// invoice_delete's measured edges: past Load the payment lookup raises before
// anything is unlinked, past Save the payment's save raises before its UPDATE
// (nothing was written before it), and past Index the payment is saved with the
// entry's audit index resolved through the resolver's fallbacks.
func TestInvoiceCoreDeleteStoredJSONBands(t *testing.T) {
	for _, test := range []struct {
		name      string
		depth     int
		recursion bool
		after     bool
		fallback  bool
	}{
		{name: "index at", depth: 9927},
		{name: "index past", depth: 9928, fallback: true},
		{name: "save at", depth: 9935, fallback: true},
		{name: "save past", depth: 9936, recursion: true, after: true},
		{name: "load at", depth: 9937, recursion: true, after: true},
		{name: "load past", depth: 9938, recursion: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			events := []string{}
			var saved []domain.InvoiceCorePaymentSave
			store := invoiceCoreEdgesStore{tx: invoiceCoreEdgesTx{depth: test.depth, events: &events, saved: &saved}}
			service := NewInvoiceCore(store, nil, func() time.Time { return time.Date(2026, 11, 9, 10, 0, 0, 0, time.UTC) })
			user := &domain.Account{ID: "owner"}
			err := service.Delete(context.Background(), InvoiceCoreRequest{Identity: domain.ResolvedIdentity{Actor: user, Effective: user,
				ScopeUser: user}}, "a1000000-0000-4000-8000-000000995010")
			if !test.recursion {
				if err != nil {
					t.Fatalf("err=%v", err)
				}
				if len(saved) != 1 || saved[0].IndexFallback != test.fallback {
					t.Fatalf("saved %+v: fallback want %v", saved, test.fallback)
				}
				if !containsEvent(events, "commit") || !containsEvent(events, "invoice-delete") {
					t.Fatalf("events %v: a write is missing", events)
				}
				return
			}
			var recursion *PaymentStoredJSONRecursionError
			if !errors.As(err, &recursion) {
				t.Fatalf("err=%v, want the recursion error", err)
			}
			if recursion.AfterWrites != test.after {
				t.Fatalf("AfterWrites=%v want %v", recursion.AfterWrites, test.after)
			}
			if recursion.Text != recursionArrayText {
				t.Fatalf("Text=%q", recursion.Text)
			}
			if containsEvent(events, "commit") || containsEvent(events, "payment-save") || containsEvent(events, "payment-audit") ||
				containsEvent(events, "invoice-delete") {
				t.Fatalf("events %v: a failed request wrote", events)
			}
			if len(saved) != 0 {
				t.Fatalf("saved %+v on a failed save", saved)
			}
		})
	}
}
