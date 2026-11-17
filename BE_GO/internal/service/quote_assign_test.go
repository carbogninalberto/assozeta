package service

import (
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// association_quote_assign's stack as a Celery worker has not been measured
// (owner decision X15): a stored payment of one of its subscriptions past the
// worker band is handed to Django like the job's other refusals, and below it
// every measured stack parses the row.
func TestQuoteAssignStoredPaymentJSONWorkerBand(t *testing.T) {
	if paymentJSONWorkerBand != 9800 {
		t.Fatalf("band=%d, want 9800", paymentJSONWorkerBand)
	}
	for _, test := range []struct {
		name    string
		text    *string
		refusal bool
	}{
		{name: "no columns", text: nil},
		{name: "shallow", text: jsonEdgesArray(12)},
		{name: "below the band", text: jsonEdgesArray(paymentJSONWorkerBand - 1)},
		{name: "at the band", text: jsonEdgesArray(paymentJSONWorkerBand), refusal: true},
		{name: "past the band", text: jsonEdgesArray(paymentJSONWorkerBand + 1), refusal: true},
	} {
		rows := []domain.QuoteAssignSubscription{{ID: "subscription", PaymentMetaPaymentCategories: test.text}}
		err := quoteAssignStoredJSON(rows)
		if !test.refusal {
			if err != nil {
				t.Fatalf("%s: err=%v", test.name, err)
			}
			continue
		}
		if !errors.Is(err, errPaymentStoredJSONWorkerBand) || !errors.Is(err, refusal.ErrNotPorted) {
			t.Fatalf("%s: err=%v, want the worker handoff", test.name, err)
		}
		if errors.Is(err, refusal.ErrAppState) {
			t.Fatalf("%s: the worker band must stay a handoff", test.name)
		}
	}

	// The job's own creates keep the worker band as their load, save and index
	// edge: a row deeper than the band is the handoff, one at it is parsed.
	edges := paymentJSONEdges{Load: paymentJSONWorkerBand - 1, Save: paymentJSONWorkerBand - 1, Index: paymentJSONWorkerBand - 1}
	if _, err := paymentJSONStoredEdges(nil, jsonEdgesArray(paymentJSONWorkerBand-1), nil, edges); err != nil {
		t.Fatalf("below the band: %v", err)
	}
	if _, err := paymentJSONStoredEdges(nil, jsonEdgesArray(paymentJSONWorkerBand), nil, edges); !errors.As(err, new(*PaymentStoredJSONRecursionError)) {
		t.Fatalf("at the band: %v, want the recursion error", err)
	}
}
