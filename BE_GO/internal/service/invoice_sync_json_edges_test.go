package service

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// invoiceSyncEdgesStore is invoice_update's store: one invoice whose payment
// carries the deep column, with every write recorded.
type invoiceSyncEdgesStore struct {
	domain.InvoiceSyncStore
	depth  int
	events *[]string
	audits *[]domain.PaymentArchiveEvent
}

func invoiceSyncText(value string) *string { return &value }

func (s invoiceSyncEdgesStore) log(event string) { *s.events = append(*s.events, event) }

func (s invoiceSyncEdgesStore) BeginInvoiceSync(context.Context) (domain.InvoiceSyncTransaction, error) {
	return invoiceSyncEdgesTx{invoiceSyncEdgesStore: s}, nil
}

func (s invoiceSyncEdgesStore) AppendInvoiceAudit(context.Context, string, string, map[string][2]string, domain.LoginAuditContext,
	time.Time) error {
	s.log("invoice-audit")
	return nil
}

func (s invoiceSyncEdgesStore) SaveSyncInvoice(context.Context, domain.InvoiceSyncSave) error {
	s.log("invoice-save")
	return nil
}

func (s invoiceSyncEdgesStore) RenderInvoice(context.Context, string) (*domain.PaymentReadsInvoice, error) {
	return &domain.PaymentReadsInvoice{ID: "invoice", AssociationID: "assoc", Attached: "[]", MembershipFee: "0.00",
		ActivityFee: "10.00", CreationDate: time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)}, nil
}

type invoiceSyncEdgesTx struct {
	domain.InvoiceSyncTransaction
	invoiceSyncEdgesStore
}

func (f invoiceSyncEdgesTx) ScopedAssociation(context.Context, string, *string) (string, error) {
	return "assoc", nil
}

func (f invoiceSyncEdgesTx) SyncInvoice(context.Context, string, string, *string, *string) (*domain.InvoiceSyncRow, error) {
	return &domain.InvoiceSyncRow{ID: "invoice", AssociationID: "assoc", Description: invoiceSyncText("Vecchia")}, nil
}

func (f invoiceSyncEdgesTx) InvoicePayment(context.Context, string, *string, *string) (*domain.PaymentStateRow, error) {
	return &domain.PaymentStateRow{PaymentSaveRow: domain.PaymentSaveRow{ID: "payment", AssociationID: "assoc", Subject: 0,
		CategoryID: invoiceSyncText("category"), CategoryName: invoiceSyncText("Quote"), Description: invoiceSyncText("Quota"),
		Course: jsonEdgesArray(f.depth)}}, nil
}

func (f invoiceSyncEdgesTx) SaveInvoicePayment(context.Context, string, *time.Time, *string, *string) error {
	f.log("payment-save")
	return nil
}

func (f invoiceSyncEdgesTx) AppendPaymentAudit(_ context.Context, event domain.PaymentArchiveEvent) error {
	*f.audits = append(*f.audits, event)
	f.log("payment-audit")
	return nil
}

func (f invoiceSyncEdgesTx) InvoiceGeneratePayment(context.Context, string) (domain.InvoiceGeneratePayment, error) {
	return domain.InvoiceGeneratePayment{Amount: "10.00", CreationDate: time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)}, nil
}

func (f invoiceSyncEdgesTx) AssociationDenomination(context.Context, string) (*string, error) {
	return invoiceSyncText("Denominazione"), nil
}

func (f invoiceSyncEdgesTx) Commit() error {
	f.log("commit")
	return nil
}

func (f invoiceSyncEdgesTx) Rollback() error {
	f.log("rollback")
	return nil
}

// invoice_update's measured edges: past Load the payment lookup raises before
// the request writes, past Save the payment's save raises before the payment
// row is updated (nothing was written before it), and past Index the payment is
// saved with the entry's audit index resolved through the resolver's fallbacks.
func TestInvoiceSyncUpdateStoredJSONBands(t *testing.T) {
	for _, test := range []struct {
		name      string
		depth     int
		recursion bool
		after     bool
		fallback  bool
		commits   bool
	}{
		{name: "index at", depth: 9925, commits: true},
		{name: "index past", depth: 9926, fallback: true, commits: true},
		{name: "save at", depth: 9933, fallback: true, commits: true},
		{name: "save past", depth: 9934, recursion: true, after: true},
		{name: "load at", depth: 9935, recursion: true, after: true},
		{name: "load past", depth: 9936, recursion: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			events := []string{}
			var audits []domain.PaymentArchiveEvent
			store := invoiceSyncEdgesStore{depth: test.depth, events: &events, audits: &audits}
			service := NewInvoiceSyncUpdates(store, NewInvoiceSyncPrint(&invoiceSyncAdmission{}, ""),
				func() time.Time { return time.Date(2026, 11, 9, 10, 0, 0, 0, time.UTC) })
			owner := &domain.Account{ID: "owner"}
			body := domain.RequestObject{{Name: "number", Value: json.Number("5")}, {Name: "payment_date", Value: "2026-05-01"}}
			_, err := service.Update(context.Background(), domain.ResolvedIdentity{Actor: owner, Effective: owner, ScopeUser: owner},
				InvoiceSyncUpdateRequest{UID: "a1000000-0000-4000-8000-000000995010", Body: body})
			if !test.recursion {
				if err != nil {
					t.Fatalf("err=%v", err)
				}
				if len(audits) != 1 || audits[0].IndexFallback != test.fallback {
					t.Fatalf("audits %+v: fallback want %v", audits, test.fallback)
				}
				if !containsEvent(events, "payment-save") || !containsEvent(events, "invoice-save") {
					t.Fatalf("events %v: a write is missing", events)
				}
				if test.commits != containsEvent(events, "commit") {
					t.Fatalf("events %v: commit want %v", events, test.commits)
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
			if containsEvent(events, "commit") || containsEvent(events, "payment-save") || containsEvent(events, "invoice-save") ||
				containsEvent(events, "payment-audit") {
				t.Fatalf("events %v: a failed request wrote", events)
			}
			if len(audits) != 0 {
				t.Fatalf("audits %+v on a failed save", audits)
			}
		})
	}
}
