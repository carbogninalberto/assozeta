package service

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"github.com/google/uuid"
)

// stripePaidCheckEdgesTx records what the job's mark wrote.
type stripePaidCheckEdgesTx struct {
	domain.PaymentPaidFlowTransaction
	locked    domain.StripePayment
	types     int
	marks     int
	commits   int
	rollbacks int
	events    []domain.PaymentArchiveEvent
}

func (t *stripePaidCheckEdgesTx) SaveStripePaidCheckType(context.Context, domain.StripePaidCheckPayment, time.Time) error {
	t.types++
	return nil
}

func (t *stripePaidCheckEdgesTx) StripePaidCheckPayments(context.Context) ([]string, error) {
	return nil, nil
}

func (t *stripePaidCheckEdgesTx) LockStripePaidCheck(context.Context, string) (*domain.StripePaidCheckPayment, error) {
	return nil, nil
}

func (t *stripePaidCheckEdgesTx) SupplierInvoice(context.Context, string) (*domain.PaymentStateSupplierInvoice, error) {
	return nil, nil
}

func (t *stripePaidCheckEdgesTx) LockStripePayment(context.Context, string, *string) (*domain.StripePayment, error) {
	row := t.locked
	return &row, nil
}

func (t *stripePaidCheckEdgesTx) MarkStripePaymentPaid(context.Context, string, time.Time) (string, error) {
	t.marks++
	return "default", nil
}

func (t *stripePaidCheckEdgesTx) AppendPaymentAudit(_ context.Context, event domain.PaymentArchiveEvent) error {
	t.events = append(t.events, event)
	return nil
}

func (t *stripePaidCheckEdgesTx) CompleteJob(context.Context, uuid.UUID, uuid.UUID) error { return nil }

func (t *stripePaidCheckEdgesTx) Commit() error   { t.commits++; return nil }
func (t *stripePaidCheckEdgesTx) Rollback() error { t.rollbacks++; return nil }

// The paid check's worker stack is not measured: X15 uses the webhook's
// measured band (owner decision), the HTTP site that runs the same
// mark_payment_as_paid. The task's list() parses up to the load edge; a
// candidate past it ends the task before any payment runs (the plan's first
// pass). Inside mark, a row up to the save edge is written (the audit index
// falls back past Index), one past it raises after the type save, and one past
// the load edge raises at the block's own load. Both are RecursionErrors inside
// the thread-pool future: counted failures, and the loop goes on.
func TestStripePaidCheckJSONEdge(t *testing.T) {
	for _, test := range []struct {
		name      string
		depth     int
		after     bool
		recursion bool
		fallback  bool
	}{
		{name: "reference parser depth", depth: 2000},
		{name: "measured views' depth", depth: 9000},
		{name: "index at", depth: stripePaidCheckJSONEdges.Index},
		{name: "index past", depth: stripePaidCheckJSONEdges.Index + 1, fallback: true},
		{name: "save at", depth: stripePaidCheckJSONEdges.Save, fallback: true},
		{name: "save past", depth: stripePaidCheckJSONEdges.Save + 1, recursion: true, after: true},
		{name: "load at", depth: stripePaidCheckJSONEdges.Load, recursion: true, after: true},
		{name: "load past", depth: stripePaidCheckJSONEdges.Load + 1, recursion: true},
	} {
		category, description, invoice, user := "category", "Quota", "invoice", "user"
		depth := test.depth
		locked := domain.StripePayment{PaymentSaveRow: domain.PaymentSaveRow{ID: stripeEdgesID, AssociationID: "association",
			Subject: 0, CategoryID: &category, CategoryName: &category, Description: &description, Course: jsonEdgesArray(depth)},
			Amount: "10.00", InvoiceID: &invoice, UserID: &user}
		tx := &stripePaidCheckEdgesTx{locked: locked}
		job := NewStripePaidCheck(nil, stripeEdgesRoutes(&stripeEdgesStore{}), nil)
		row := domain.StripePaidCheckPayment{ID: stripeEdgesID, Type: "default", UserID: &user, InvoiceID: &invoice,
			Course: jsonEdgesArray(depth)}
		_, err := job.mark(context.Background(), tx, job.stripe, row)
		if test.recursion {
			var recursion *PaymentStoredJSONRecursionError
			if !errors.As(err, &recursion) {
				t.Fatalf("%s: err=%v, want the stored JSON recursion error", test.name, err)
			}
			if recursion.AfterWrites != test.after {
				t.Errorf("%s: AfterWrites=%v, want %v", test.name, recursion.AfterWrites, test.after)
			}
			if !stripePaidCheckPython(err) {
				t.Errorf("%s: %v must count as a failed update", test.name, err)
			}
			if errors.Is(err, refusal.ErrNotPorted) {
				t.Errorf("%s: %v must be Django's RecursionError, not a hand-off", test.name, err)
			}
			if tx.marks != 0 || len(tx.events) != 0 {
				t.Errorf("%s: marks=%d events=%d, want no save (the block is rolled back)", test.name, tx.marks,
					len(tx.events))
			}
			continue
		}
		if err != nil {
			t.Fatalf("%s: err=%v", test.name, err)
		}
		if tx.types != 1 || tx.marks != 1 {
			t.Errorf("%s: types=%d marks=%d, want the save (the caller commits the block)", test.name, tx.types, tx.marks)
		}
		if len(tx.events) != 1 || tx.events[0].IndexFallback != test.fallback {
			t.Errorf("%s: events=%+v, want one entry (fallback %v)", test.name, tx.events, test.fallback)
		}
	}
}

// stripePaidCheckPlanStore serves the task's candidate list and its locked
// rows; only the planning transaction's reads are needed.
type stripePaidCheckPlanStore struct {
	ids       []string
	rows      map[string]domain.StripePaidCheckPayment
	rollbacks int
}

func (s *stripePaidCheckPlanStore) BeginStripePaidCheck(context.Context, domain.Job) (domain.StripePaidCheckTransaction, error) {
	return &stripePaidCheckPlanTx{store: s}, nil
}

type stripePaidCheckPlanTx struct {
	domain.StripePaidCheckTransaction
	store *stripePaidCheckPlanStore
}

func (t *stripePaidCheckPlanTx) StripePaidCheckPayments(context.Context) ([]string, error) {
	return t.store.ids, nil
}

func (t *stripePaidCheckPlanTx) LockStripePaidCheck(_ context.Context, id string) (*domain.StripePaidCheckPayment, error) {
	row, ok := t.store.rows[id]
	if !ok {
		return nil, nil
	}
	return &row, nil
}

func (t *stripePaidCheckPlanTx) Rollback() error { t.store.rollbacks++; return nil }

// Django's list() loads every candidate's JSON before the pool starts, so one
// payment past the load edge ends the task with no payment processed, whatever
// the ids' order. The planning transaction is rolled back either way.
func TestStripePaidCheckPlanStopsOnStoredJSONRecursion(t *testing.T) {
	store := &stripePaidCheckPlanStore{
		ids: []string{"deep", "shallow"},
		rows: map[string]domain.StripePaidCheckPayment{
			"deep":    {ID: "deep", Course: jsonEdgesArray(stripePaidCheckJSONEdges.Load + 1)},
			"shallow": {ID: "shallow"},
		},
	}
	job := NewStripePaidCheck(store, stripeEdgesRoutes(&stripeEdgesStore{}),
		slog.New(slog.NewTextHandler(io.Discard, nil)))
	ids, err := job.plan(context.Background(), domain.Job{})
	if err != nil {
		t.Fatalf("plan: %v", err)
	}
	if ids != nil {
		t.Fatalf("ids=%v, want no payment processed", ids)
	}
	if store.rollbacks == 0 {
		t.Fatal("the planning transaction was not rolled back")
	}
}
