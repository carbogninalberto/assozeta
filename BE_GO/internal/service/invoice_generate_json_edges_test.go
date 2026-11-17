package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

const invoiceGenerateTestAssociation = "a1000000-0000-4000-8000-000000701001"

func invoiceGenerateEdgesFake(depth int, events *[]string, audits *[]domain.PaymentArchiveEvent) invoiceGenerateFakeStore {
	category, name, description := "c1", "Quote", "Quota"
	return invoiceGenerateFakeStore{tx: invoiceGenerateFake{events: events, audits: audits, payment: &domain.PaymentStateRow{
		PaymentSaveRow: domain.PaymentSaveRow{ID: "p1", AssociationID: invoiceGenerateTestAssociation, CategoryID: &category,
			CategoryName: &name, Description: &description, Course: jsonEdgesArray(depth)}}}}
}

// payment_generate_invoice's measured edges: a stored column past Load raises at
// the view's own load (nothing is written), past Save at the payment's save
// (the view's transaction is atomic, so its invoice and its entries are rolled
// back), and past Index saves with the audit index resolved through the
// resolver's fallbacks.
func TestInvoiceGenerateStoredJSONBands(t *testing.T) {
	for _, test := range []struct {
		name              string
		depth             int
		recursion         bool
		after             bool
		fallback          bool
		wantCommit        bool
		wantInvoiceCreate bool
	}{
		{name: "index at", depth: 9925, wantCommit: true},
		{name: "index past", depth: 9926, fallback: true, wantCommit: true},
		{name: "save at", depth: 9933, fallback: true, wantCommit: true},
		{name: "save past", depth: 9934, recursion: true, after: true, wantInvoiceCreate: true},
		{name: "load at", depth: 9935, recursion: true, after: true, wantInvoiceCreate: true},
		{name: "load past", depth: 9936, recursion: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			events := []string{}
			var audits []domain.PaymentArchiveEvent
			publisher := &invoiceOrderedPublisher{events: &events}
			service := NewInvoiceGenerations(invoiceGenerateEdgesFake(test.depth, &events, &audits), publisher,
				func() time.Time { return time.Date(2026, 11, 9, 10, 0, 0, 0, time.UTC) })
			service.newID = func() string { return "i1" }
			owner := &domain.Account{ID: "owner"}
			body := domain.RequestObject{{Name: "subscription_id", Value: "a1000000-0000-4000-8000-000000995009"}}
			_, err := service.Generate(context.Background(), domain.ResolvedIdentity{Actor: owner, Effective: owner, ScopeUser: owner},
				InvoiceGenerateRequest{ID: "a1000000-0000-4000-8000-000000995010"}, body)
			if !test.recursion {
				if err != nil {
					t.Fatalf("err=%v", err)
				}
				if !containsEvent(events, "commit") {
					t.Fatalf("events %v: the save was not committed", events)
				}
				if !containsEvent(events, "create 8") || !containsEvent(events, "save") {
					t.Fatalf("events %v: the invoice or the payment was not written", events)
				}
				if len(audits) != 1 || audits[0].IndexFallback != test.fallback {
					t.Fatalf("audits %+v: fallback want %v", audits, test.fallback)
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
			if containsEvent(events, "commit") {
				t.Fatalf("events %v: a failed request committed", events)
			}
			if containsEvent(events, "save") {
				t.Fatalf("events %v: the payment was saved", events)
			}
			// The view's transaction is atomic: the invoice it wrote before the
			// payment's save is rolled back with the failed request.
			if containsEvent(events, "create 8") != test.wantInvoiceCreate {
				t.Fatalf("events %v: invoice written=%v want %v", events, containsEvent(events, "create 8"), test.wantInvoiceCreate)
			}
			if len(audits) != 0 {
				t.Fatalf("audits %+v on a failed load", audits)
			}
		})
	}
}

func containsEvent(events []string, want string) bool {
	for _, event := range events {
		if event == want {
			return true
		}
	}
	return false
}
