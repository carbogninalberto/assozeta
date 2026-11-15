package service

import (
	"errors"
	"fmt"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// The long-tail conversions (agent C, PR 3): states Django raises on and
// states only the application writes (long-tail rules 2 and 3) are
// application-state errors. They answer Django's 500 page and are never
// handed off, so none of them may match refusal.ErrNotPorted.
func TestLongTailPR3AppStateErrors(t *testing.T) {
	for name, err := range map[string]error{
		"ErrInvoiceSyncPrintDeferred":    ErrInvoiceSyncPrintDeferred,
		"ErrSupplierOrdering":            ErrSupplierOrdering,
		"ErrSubscriptionUpdateNotPorted": ErrSubscriptionUpdateNotPorted,
		"errInstanceBackupsRunnerDepth":  errInstanceBackupsRunnerDepth,
		"errPersonaPictureKey":           errPersonaPictureKey,
		"errOnboardingFeeFloat":          errOnboardingFeeFloat,
	} {
		wrapped := fmt.Errorf("wrapped: %w", err)
		if _, ok := refusal.AppStateReason(wrapped); !ok || !errors.Is(wrapped, refusal.ErrAppState) {
			t.Errorf("%s is not an application-state error", name)
		}
		if errors.Is(wrapped, refusal.ErrNotPorted) {
			t.Errorf("%s is still a refusal: it would be handed off", name)
		}
		if !errors.Is(wrapped, err) {
			t.Errorf("%s no longer matches itself through errors.Is", name)
		}
	}
}
