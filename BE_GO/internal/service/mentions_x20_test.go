package service

import (
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// TestFieldResolverX20: a mention path outside the modelled fields renders as
// the empty text (accepted divergence X20), the answer get_nested_attr gives
// when getattr(obj, name, None) cannot resolve a name.
func TestFieldResolverX20(t *testing.T) {
	resolve := fieldResolver(
		map[string]struct{}{"first_name": {}, "subscription_set": {}},
		map[string]*string{"first_name": ptr("Mario")},
	)
	for _, test := range []struct{ path, want string }{
		{"first_name", "Mario"},
		{"subscription_set", ""},
		{"subscription_set.count", ""},
		{"unknown", ""},
	} {
		got, err := resolve(test.path)
		if err != nil || got != test.want {
			t.Errorf("%s: %q, %v, want %q, nil", test.path, got, err, test.want)
		}
	}
}

// TestInvoiceViewsAssociateResolverX20: the invoice view's associate carries
// the selected (or main) tutor and is_minor
// (BE/docmanager/views/printing_views.py:427-430); its tutor paths read the
// tutor's Associate, and str() of the other two is not modelled (X20).
func TestInvoiceViewsAssociateResolverX20(t *testing.T) {
	associate := &domain.ViewAssociate{FirstName: ptr("Anna")}
	tutor := &domain.ViewAssociate{FirstName: ptr("Luca")}
	resolve := invoiceViewsAssociateResolver(associate, tutor)
	for _, test := range []struct{ path, want string }{
		{"first_name", "Anna"},
		{"tutor.first_name", "Luca"},
		{"tutor", ""},
		{"is_minor", ""},
	} {
		got, err := resolve(test.path)
		if err != nil || got != test.want {
			t.Errorf("%s: %q, %v, want %q, nil", test.path, got, err, test.want)
		}
	}

	// getattr on None answers "" for every path.
	noTutor := invoiceViewsAssociateResolver(associate, nil)
	if got, err := noTutor("tutor.first_name"); err != nil || got != "" {
		t.Errorf("tutor.first_name without a tutor: %q, %v, want \"\", nil", got, err)
	}
}
