package service

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"strconv"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// stripeEdgesConfig has no stored integration: the environment keys answer.
type stripeEdgesConfig struct{}

func (stripeEdgesConfig) SocialConfiguration(context.Context) (*domain.SocialConfiguration, error) {
	return nil, nil
}

// stripeEdgesStore serves one payment for mark and the views' own loads.
type stripeEdgesStore struct {
	tx   *stripeEdgesTx
	row  domain.StripePayment
	rows []domain.StripePayment
	bill *domain.PaymentStateSupplierInvoice
}

func (s *stripeEdgesStore) BeginStripe(context.Context) (domain.StripeRoutesTransaction, error) {
	return s.tx, nil
}

func (s *stripeEdgesStore) PaymentByID(context.Context, string, domain.StripePaymentQuery) (*domain.StripePayment, error) {
	row := s.row
	return &row, nil
}

func (s *stripeEdgesStore) PaymentByIntent(context.Context, string, domain.StripePaymentQuery) (*domain.StripePayment, error) {
	row := s.row
	return &row, nil
}

func (s *stripeEdgesStore) PaymentsByIntent(context.Context, domain.StripeIntentMatch, domain.StripePaymentQuery) ([]domain.StripePayment, error) {
	return s.rows, nil
}

func (s *stripeEdgesStore) PaymentIDs(_ context.Context, ids []string, _ bool, _ domain.StripePaymentQuery) ([]string, error) {
	return ids, nil
}

func (s *stripeEdgesStore) AssociationCheckout(context.Context, string, *string) (bool, *string, error) {
	return false, nil, nil
}

// stripeEdgesTx satisfies both the stripe transaction and the paid flow's, so
// mark's type assertion takes the paid-flow path; a payment with an invoice
// skips that flow.
type stripeEdgesTx struct {
	domain.PaymentPaidFlowTransaction
	bill      *domain.PaymentStateSupplierInvoice
	row       domain.StripePayment
	marks     int
	commits   int
	rollbacks int
	events    []domain.PaymentArchiveEvent
}

func (t *stripeEdgesTx) LockStripePayment(context.Context, string, *string) (*domain.StripePayment, error) {
	row := t.row
	return &row, nil
}

func (t *stripeEdgesTx) SavePaymentIntent(context.Context, string, *string, *string, *string) error {
	return nil
}

func (t *stripeEdgesTx) SupplierInvoice(context.Context, string) (*domain.PaymentStateSupplierInvoice, error) {
	return t.bill, nil
}

func (t *stripeEdgesTx) MarkStripePaymentPaid(context.Context, string, time.Time) (string, error) {
	t.marks++
	return "default", nil
}

func (t *stripeEdgesTx) AppendPaymentAudit(_ context.Context, event domain.PaymentArchiveEvent) error {
	t.events = append(t.events, event)
	return nil
}

func (t *stripeEdgesTx) RenderPayment(_ context.Context, id string, _ *string) (domain.PaymentStateRender, error) {
	row := t.row
	return domain.PaymentStateRender{Payment: domain.CourseSubscriptionPayment{InvoicePartyPayment: domain.InvoicePartyPayment{
		ID: id, MetaPaymentCategories: row.MetaPaymentCategories, Meta: row.Meta, Course: row.Course}}}, nil
}

func (t *stripeEdgesTx) Commit() error   { t.commits++; return nil }
func (t *stripeEdgesTx) Rollback() error { t.rollbacks++; return nil }

const stripeEdgesID = "a1000000-0000-4000-8000-000001520001"

func stripeEdgesPayment(depth int) domain.StripePayment {
	category, description, invoice, user := "category", "Quota", "invoice", "user"
	return domain.StripePayment{PaymentSaveRow: domain.PaymentSaveRow{ID: stripeEdgesID, AssociationID: "association", Subject: 0,
		CategoryID: &category, CategoryName: &category, Description: &description, Course: jsonEdgesArray(depth)},
		Amount: "10.00", Paid: false, InvoiceID: &invoice, UserID: &user, HasAssociation: true}
}

func stripeEdgesRoutes(store *stripeEdgesStore) *StripeRoutes {
	return NewStripeRoutes(store, stripeEdgesConfig{}, StripeEnvironmentKeys{WebhookSecret: "whsec_edges"},
		"", nil, func() time.Time { return time.Date(2026, 11, 17, 9, 0, 0, 0, time.UTC) })
}

// mark_payment_as_paid's bands, once per stripe view's measured edges: the
// UPDATE runs up to auditlog's refetch (Save), the index entry falls back past
// Index, and past the load edge (Load) the block is the 500 page. The block is
// atomic, so both recursion bands leave nothing written. stripe_multiple_pay's
// binding load equals its save edge, so its recursion band has no writes yet.
func TestStripeMarkJSONEdgeBands(t *testing.T) {
	for _, route := range []struct {
		name  string
		edges paymentJSONEdges
	}{
		{name: "pay", edges: stripePayJSONEdges},
		{name: "multiple-pay", edges: stripeMultiplePayJSONEdges},
		{name: "webhook", edges: stripeWebhookJSONEdges},
	} {
		edges := route.edges
		for _, depth := range []int{edges.Index, edges.Index + 1, edges.Save, edges.Save + 1, edges.Load, edges.Load + 1} {
			recurses := depth > edges.Save
			after := recurses && depth <= edges.Load
			fallback := !recurses && depth > edges.Index
			name := "depth " + strconv.Itoa(depth)
			payment := stripeEdgesPayment(depth)
			tx := &stripeEdgesTx{row: payment}
			routes := stripeEdgesRoutes(&stripeEdgesStore{tx: tx, row: payment})
			ctx := withPaymentJSONEdges(context.Background(), route.edges)
			err := routes.mark(ctx, domain.ResolvedIdentity{}, StripeRequest{}, &payment, true)
			if recurses {
				var recursion *PaymentStoredJSONRecursionError
				if !errors.As(err, &recursion) {
					t.Fatalf("%s %s: err=%v, want the recursion error", route.name, name, err)
				}
				if recursion.AfterWrites != after {
					t.Errorf("%s %s: AfterWrites=%v want %v", route.name, name, recursion.AfterWrites, after)
				}
				if !errors.Is(err, refusal.ErrAppState) || errors.Is(err, refusal.ErrNotPorted) {
					t.Errorf("%s %s: err=%v, want an application-state error", route.name, name, err)
				}
				if tx.marks != 0 || len(tx.events) != 0 || tx.commits != 0 {
					t.Errorf("%s %s: marks=%d events=%d commits=%d, want no write", route.name, name, tx.marks, len(tx.events),
						tx.commits)
				}
				if tx.rollbacks == 0 {
					t.Errorf("%s %s: the transaction must be rolled back", route.name, name)
				}
				continue
			}
			if err != nil {
				t.Fatalf("%s %s: err=%v", route.name, name, err)
			}
			if tx.marks != 1 || tx.commits != 1 {
				t.Errorf("%s %s: marks=%d commits=%d, want one save and the commit", route.name, name, tx.marks, tx.commits)
			}
			if len(tx.events) != 1 {
				t.Fatalf("%s %s: events=%d want 1", route.name, name, len(tx.events))
			}
			if tx.events[0].IndexFallback != fallback {
				t.Errorf("%s %s: IndexFallback=%v want %v", route.name, name, tx.events[0].IndexFallback, fallback)
			}
		}
	}
}

// stripe_pay loads the payment itself first: past its edge the request is the
// 500 page before any Stripe exchange; at the edge it goes on to the online
// payments check.
func TestStripePayJSONLoadEdge(t *testing.T) {
	for _, test := range []struct {
		name     string
		depth    int
		recurses bool
	}{
		{name: "at", depth: stripePayJSONEdges.Load},
		{name: "past", depth: stripePayJSONEdges.Load + 1, recurses: true},
	} {
		payment := stripeEdgesPayment(test.depth)
		tx := &stripeEdgesTx{row: payment}
		store := &stripeEdgesStore{tx: tx, row: payment}
		routes := stripeEdgesRoutes(store)
		_, err := routes.Pay(context.Background(), domain.ResolvedIdentity{}, StripeRequest{}, stripeEdgesID, nil)
		if test.recurses {
			var recursion *PaymentStoredJSONRecursionError
			if !errors.As(err, &recursion) || recursion.AfterWrites || !errors.Is(err, refusal.ErrAppState) {
				t.Fatalf("%s: err=%v, want the load's recursion error", test.name, err)
			}
			if tx.commits != 0 || tx.marks != 0 {
				t.Fatalf("%s: commits=%d marks=%d, want nothing written", test.name, tx.commits, tx.marks)
			}
			continue
		}
		if errors.Is(err, refusal.ErrAppState) {
			t.Fatalf("%s: err=%v, want the online payments check", test.name, err)
		}
	}
}

// stripe_multiple_pay's serializer loads every listed row before anything is
// written: a value past its edge is the 500 page, at it the request goes on to
// the online payments check (the loop's later get and mark's lock have higher
// edges, so they cannot fire first for a listed row).
func TestStripeMultiplePayJSONLoadEdge(t *testing.T) {
	for _, test := range []struct {
		name     string
		depth    int
		recurses bool
	}{
		{name: "serializer at", depth: stripeMultiplePayJSONEdges.Load},
		{name: "serializer past", depth: stripeMultiplePayJSONEdges.Load + 1, recurses: true},
	} {
		payment := stripeEdgesPayment(test.depth)
		tx := &stripeEdgesTx{row: payment}
		store := &stripeEdgesStore{tx: tx, row: payment}
		routes := stripeEdgesRoutes(store)
		body := domain.RequestObject{{Name: "payments", Value: []any{stripeEdgesID}}}
		_, err := routes.MultiplePay(context.Background(), domain.ResolvedIdentity{}, StripeRequest{}, body)
		if test.recurses {
			var recursion *PaymentStoredJSONRecursionError
			if !errors.As(err, &recursion) || recursion.AfterWrites || !errors.Is(err, refusal.ErrAppState) {
				t.Fatalf("%s: err=%v, want the load's recursion error", test.name, err)
			}
			if tx.commits != 0 || tx.marks != 0 {
				t.Fatalf("%s: commits=%d marks=%d, want nothing written", test.name, tx.commits, tx.marks)
			}
			continue
		}
		if errors.Is(err, refusal.ErrAppState) {
			t.Fatalf("%s: err=%v, want the online payments check", test.name, err)
		}
	}
}

func stripeWebhookSignature(payload []byte, secret string, at time.Time) *string {
	stamp := strconv.FormatInt(at.Unix(), 10)
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(stamp + "."))
	mac.Write(payload)
	header := "t=" + stamp + ",v1=" + hex.EncodeToString(mac.Sum(nil))
	return &header
}

// stripe_webhook iterates the matching rows inside its try: a value past that
// load's edge is logged and answered 200 without writing anything, and a value
// past auditlog's refetch edge in mark's atomic block is answered 200 with the
// block rolled back.
func TestStripeWebhookJSONBands(t *testing.T) {
	payload := []byte(`{"type": "charge.succeeded", "data": {"object": {"payment_intent": "pi_edges"}}}`)
	at := time.Date(2026, 11, 17, 9, 0, 0, 0, time.UTC)
	signature := stripeWebhookSignature(payload, "whsec_edges", at)
	for _, test := range []struct {
		name      string
		depth     int
		wantMarks int
		fallback  bool
	}{
		{name: "index past", depth: stripeWebhookJSONEdges.Index + 1, wantMarks: 1, fallback: true},
		{name: "save past", depth: stripeWebhookJSONEdges.Save + 1},
		{name: "load past", depth: stripeWebhookJSONEdges.Load + 1},
	} {
		payment := stripeEdgesPayment(test.depth)
		tx := &stripeEdgesTx{row: payment}
		store := &stripeEdgesStore{tx: tx, row: payment, rows: []domain.StripePayment{payment}}
		routes := stripeEdgesRoutes(store)
		if err := routes.Webhook(context.Background(), domain.ResolvedIdentity{}, StripeRequest{}, payload, signature); err != nil {
			t.Fatalf("%s: err=%v, want the swallowed answer", test.name, err)
		}
		if test.wantMarks == 0 {
			if tx.marks != 0 || tx.commits != 0 {
				t.Fatalf("%s: marks=%d commits=%d, want nothing written", test.name, tx.marks, tx.commits)
			}
			continue
		}
		if tx.marks != test.wantMarks || tx.commits != 1 {
			t.Fatalf("%s: marks=%d commits=%d want %d/1", test.name, tx.marks, tx.commits, test.wantMarks)
		}
		if len(tx.events) != 1 || tx.events[0].IndexFallback != test.fallback {
			t.Fatalf("%s: events=%+v want one with IndexFallback=%v", test.name, tx.events, test.fallback)
		}
	}
}
