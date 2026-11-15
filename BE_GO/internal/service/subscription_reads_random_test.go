package service

import (
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// Django keeps '?' in checked_ordering and compiles order_by('?') to RANDOM() with the page; '-?' is not '?' and is a
// FieldError, which the service answers as the view's 500.
func TestSubscriptionReadsRandomOrdering(t *testing.T) {
	order, err := subscriptionReadsOrder("?", false, nil)
	if err != nil {
		t.Fatalf("ascending ?: %v", err)
	}
	if len(order) != 1 || order[0] != (domain.SubscriptionReadsOrder{Column: "?"}) {
		t.Fatalf("ascending ?: %#v", order)
	}
	if _, err := subscriptionReadsOrder("?", true, nil); !errors.Is(err, domain.ErrSubscriptionReadsServer) {
		t.Fatalf("descending ?: %v", err)
	}
}
