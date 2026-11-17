package service

import (
	"context"
	"errors"
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

// The Celery job has no measured worker stack: a stored value in the band where
// the recursion edge depends on the caller keeps the former handoff (a refusal,
// never the 500 page), and below it the save runs with the measured edges of
// every stack.
func TestStripePaidCheckWorkerBand(t *testing.T) {
	for _, test := range []struct {
		name     string
		depth    int
		refused  bool
		fallback bool
	}{
		{name: "reference parser depth", depth: 2000},
		{name: "measured views' depth", depth: 9000},
		{name: "below the band", depth: paymentJSONWorkerBand - 1},
		{name: "band start", depth: paymentJSONWorkerBand, refused: true},
		{name: "band", depth: 9900, refused: true},
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
		if test.refused {
			if !errors.Is(err, errPaymentStoredJSONWorkerBand) {
				t.Fatalf("%s: err=%v, want the worker-band sentinel", test.name, err)
			}
			if errors.Is(err, refusal.ErrAppState) || !errors.Is(err, refusal.ErrNotPorted) {
				t.Errorf("%s: err=%v, want the refusal, not the 500 page", test.name, err)
			}
			if tx.types != 0 || tx.marks != 0 || tx.commits != 0 || len(tx.events) != 0 {
				t.Errorf("%s: types=%d marks=%d commits=%d events=%d, want no write", test.name, tx.types, tx.marks, tx.commits,
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
		if len(tx.events) != 1 || tx.events[0].IndexFallback {
			t.Errorf("%s: events=%+v, want one entry indexed directly", test.name, tx.events)
		}
	}
}
