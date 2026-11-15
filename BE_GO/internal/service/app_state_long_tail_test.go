package service

import (
	"errors"
	"fmt"
	"testing"

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

// A runner reply Go's decoder does not read is the family's refusal, handed
// off to Django.
func TestInstanceBackupsRunnerReplyIsHandedOff(t *testing.T) {
	deep := ""
	for i := 0; i < 10000; i++ {
		deep += "["
	}
	_, _, err := instanceBackupsJSON([]byte(deep))
	if !errors.Is(err, ErrInstanceBackupsNotPorted) {
		t.Fatalf("deep runner reply: %v", err)
	}
	if !errors.Is(err, refusal.ErrNotPorted) {
		t.Fatalf("deep runner reply is not a refusal: %v", err)
	}
}

// The sites the owner sent back to the Django handoff (long tail PR 2):
// Django answers them normally, so they stay refusal sentinels.
func TestLongTailHandoffRefusals(t *testing.T) {
	for name, err := range map[string]error{
		"ErrInstanceBackupsNotPorted":  ErrInstanceBackupsNotPorted,
		"ErrInvoiceSyncPrintDeferred":  ErrInvoiceSyncPrintDeferred,
		"ErrPersonaNotPorted":          ErrPersonaNotPorted,
		"ErrSubscriptionSignNotPorted": ErrSubscriptionSignNotPorted,
		"ErrCampsPaymentNotPorted":     ErrCampsPaymentNotPorted,
		"ErrEmailRenderNotPorted":      ErrEmailRenderNotPorted,
		"ErrDocumentViewNotPorted":     ErrDocumentViewNotPorted,
	} {
		wrapped := fmt.Errorf("wrapped: %w", err)
		if !errors.Is(wrapped, refusal.ErrNotPorted) || errors.Is(wrapped, refusal.ErrAppState) {
			t.Errorf("%s must be a refusal sentinel (handed off)", name)
		}
	}
}
