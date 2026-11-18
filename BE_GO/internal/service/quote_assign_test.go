package service

import (
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// association_quote_assign materializes each plan's subscriptions (and their
// select_related payments) before the loop body: a payment nested past the load
// edge raises RecursionError there, which the task's outer except turns into
// the end of the run. The worker's stack was not measured, so X15 uses the band
// measured for the HTTP payment loads (owner decision).
func TestQuoteAssignStoredPaymentJSONEdge(t *testing.T) {
	for _, test := range []struct {
		name    string
		text    *string
		stopped bool
	}{
		{name: "no columns", text: nil},
		{name: "shallow", text: jsonEdgesArray(12)},
		{name: "below the edge", text: jsonEdgesArray(quoteAssignJSONEdges.Load - 1)},
		{name: "at the edge", text: jsonEdgesArray(quoteAssignJSONEdges.Load)},
		{name: "past the edge", text: jsonEdgesArray(quoteAssignJSONEdges.Load + 1), stopped: true},
	} {
		rows := []domain.QuoteAssignSubscription{{ID: "subscription", PaymentMetaPaymentCategories: test.text}}
		err := quoteAssignStoredJSON(rows)
		if !test.stopped {
			if err != nil {
				t.Fatalf("%s: err=%v", test.name, err)
			}
			continue
		}
		if !errors.Is(err, errPaymentMiscStop) {
			t.Fatalf("%s: err=%v, want the task's stop", test.name, err)
		}
	}

	// The run's own creates keep those edges: a row up to the save edge parses
	// (the index entry falls back past Index), one past it raises the save's
	// RecursionError.
	if fallback, err := paymentJSONStoredEdges(nil, jsonEdgesArray(quoteAssignJSONEdges.Index+1), nil, quoteAssignJSONEdges); err != nil || !fallback {
		t.Fatalf("index past: fallback=%v err=%v", fallback, err)
	}
	if _, err := paymentJSONStoredEdges(nil, jsonEdgesArray(quoteAssignJSONEdges.Save), nil, quoteAssignJSONEdges); err != nil {
		t.Fatalf("save at: %v", err)
	}
	if _, err := paymentJSONStoredEdges(nil, jsonEdgesArray(quoteAssignJSONEdges.Save+1), nil, quoteAssignJSONEdges); !errors.As(err, new(*PaymentStoredJSONRecursionError)) {
		t.Fatalf("save past: %v, want the recursion error", err)
	}
}
