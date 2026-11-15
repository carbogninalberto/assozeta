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
		"errInstanceBackupsRunnerReply":      errInstanceBackupsRunnerReply,
		"errInstanceBackupsCachedValue":      errInstanceBackupsCachedValue,
		"errInstanceBackupsTokenActor":       errInstanceBackupsTokenActor,
		"ErrInvoiceSyncPrintDeferred":        ErrInvoiceSyncPrintDeferred,
		"errPersonaDeleteUnwired":            errPersonaDeleteUnwired,
		"errPersonaStorage":                  errPersonaStorage,
		"errPersonaTutorKey":                 errPersonaTutorKey,
		"errPersonaSubsFormDepth":            errPersonaSubsFormDepth,
		"ErrDocumentRenderAppState":          ErrDocumentRenderAppState,
		"errDocumentViewSignature":           errDocumentViewSignature,
		"errDocumentViewCollaboratorOwner":   errDocumentViewCollaboratorOwner,
		"ErrBakneySSOUnsupported":            ErrBakneySSOUnsupported,
		"errCourseEnrolmentRenderedPayment":  errCourseEnrolmentRenderedPayment,
		"errCourseLocationNoStorage":         errCourseLocationNoStorage,
		"errSubscriptionMembershipNoStorage": errSubscriptionMembershipNoStorage,
		"ErrSubscriptionSignNotPorted":       ErrSubscriptionSignNotPorted,
		"errCampsWalkOrder":                  errCampsWalkOrder,
		"errCampsGroupRemoved":               errCampsGroupRemoved,
		"errEmailRenderEarlyYear":            errEmailRenderEarlyYear,
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

// The runner reply refusal of the instance admin updates keeps its mapping:
// a reply Go's decoder does not read is still that family's refusal.
func TestInstanceBackupsRunnerReplyIsAppState(t *testing.T) {
	deep := ""
	for i := 0; i < 10000; i++ {
		deep += "["
	}
	if _, _, err := instanceBackupsJSON([]byte(deep)); !errors.Is(err, errInstanceBackupsRunnerReply) {
		t.Fatalf("deep runner reply: %v", err)
	}
}
