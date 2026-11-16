package service

import (
	"errors"
	"fmt"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// The long-tail conversions (agent C, PR 2): states only the app writes,
// inputs Django itself raises on, and external or timing outcomes are
// application-state errors. They answer Django's 500 page and are never
// handed off, so none of them may match refusal.ErrNotPorted.
func TestLongTailAppStateErrors(t *testing.T) {
	for name, err := range map[string]error{
		"errInstanceBackupsCachedValue":      errInstanceBackupsCachedValue,
		"errInstanceBackupsTokenActor":       errInstanceBackupsTokenActor,
		"errPersonaDeleteUnwired":            errPersonaDeleteUnwired,
		"errPersonaTutorKey":                 errPersonaTutorKey,
		"errPersonaSubsFormDepth":            errPersonaSubsFormDepth,
		"ErrDocumentRenderAppState":          ErrDocumentRenderAppState,
		"errDocumentViewSignature":           errDocumentViewSignature,
		"errDocumentViewCollaboratorOwner":   errDocumentViewCollaboratorOwner,
		"ErrBakneySSOUnsupported":            ErrBakneySSOUnsupported,
		"errCourseEnrolmentRenderedPayment":  errCourseEnrolmentRenderedPayment,
		"errCourseLocationNoStorage":         errCourseLocationNoStorage,
		"errSubscriptionMembershipNoStorage": errSubscriptionMembershipNoStorage,
		"errCampsGroupRemoved":               errCampsGroupRemoved,
		"ErrPaymentStateUnreachable":         domain.ErrPaymentStateUnreachable,
		"ErrStripeUnreachable":               domain.ErrStripeUnreachable,
		"ErrInvoiceCoreUnreachable":          domain.ErrInvoiceCoreUnreachable,
		"ErrPaymentReadsUnreachable":         domain.ErrPaymentReadsUnreachable,
		"ErrStatisticUnreachable":            domain.ErrStatisticUnreachable,
		"ErrCalendarUnreachable":             domain.ErrCalendarUnreachable,
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
	if errors.Is(errPaymentReadsLazyOrder, refusal.ErrNotPorted) {
		t.Error("errPaymentReadsLazyOrder is an internal marker, not a refusal")
	}
}

// A runner reply nested past the decoder's limit is a state only a runner
// that is not this application's could write: the 500 page, never a handoff.
func TestInstanceBackupsRunnerReplyDepthIsAppState(t *testing.T) {
	deep := strings.Repeat("[", 10000)
	_, _, err := instanceBackupsJSON([]byte(deep))
	if !errors.Is(err, errInstanceBackupsRunnerDepth) {
		t.Fatalf("deep runner reply: %v", err)
	}
	if !errors.Is(err, refusal.ErrAppState) {
		t.Fatalf("deep runner reply is not an application-state error: %v", err)
	}
	if errors.Is(err, refusal.ErrNotPorted) {
		t.Fatalf("deep runner reply is handed off: %v", err)
	}
}

// The sites the owner sent back to the Django handoff (long tail PR 2):
// Django answers them normally, so they stay refusal sentinels.
func TestLongTailHandoffRefusals(t *testing.T) {
	for name, err := range map[string]error{
		"ErrPersonaNotPorted":      ErrPersonaNotPorted,
		"ErrCampsPaymentNotPorted": ErrCampsPaymentNotPorted,
	} {
		wrapped := fmt.Errorf("wrapped: %w", err)
		if !errors.Is(wrapped, refusal.ErrNotPorted) || errors.Is(wrapped, refusal.ErrAppState) {
			t.Errorf("%s must be a refusal sentinel (handed off)", name)
		}
	}
}
