package service

import (
	"errors"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// deepFormJSON builds a form JSON document whose maximum nesting is depth.
func deepFormJSON(depth int) string { return strings.Repeat("[", depth) + strings.Repeat("]", depth) }

// TestAutomationWorkflowFormTreeBands pins communications/workflows/<id>/update
// (owner decision X15): D <= 9937 normal, 9938 saved then the 500 page,
// 9939..9941 the audit entry kept without the row update, D >= 9942 nothing
// written.
func TestAutomationWorkflowFormTreeBands(t *testing.T) {
	for _, test := range []struct {
		depth              int
		savedThenFailed    bool
		auditOnly, refused bool
	}{
		{0, false, false, false},
		{600, false, false, false},
		{9937, false, false, false},
		{9938, true, false, false},
		{9939, false, true, false},
		{9940, false, true, false},
		{9941, false, true, false},
		{9942, false, false, true},
		{10000, false, false, true},
	} {
		savedThenFailed, auditOnly, refused := automationWorkflowFormTreeBand(test.depth)
		if savedThenFailed != test.savedThenFailed || auditOnly != test.auditOnly || refused != test.refused {
			t.Fatalf("depth %d: got %v %v %v, want %v %v %v", test.depth, savedThenFailed, auditOnly, refused,
				test.savedThenFailed, test.auditOnly, test.refused)
		}
	}
	for _, err := range []error{errAutomationWorkflowFormTreeSavedThenFailed, errAutomationWorkflowFormTreeTooDeep} {
		if !errors.Is(err, refusal.ErrAppState) || errors.Is(err, refusal.ErrNotPorted) {
			t.Fatalf("%v is not an application-state error", err)
		}
	}
}

// TestAutomationWorkflowPatchFormTreeDepth pins the parser's depth on the
// measured band, including a value past 500 levels.
func TestAutomationWorkflowPatchFormTreeDepth(t *testing.T) {
	for _, depth := range []int{501, 9937, 9938, 9941, 9942} {
		patch, invalid, err := dto.ParseAutomationWorkflowPatch(
			domain.RequestObject{{Name: "automation_tree", Value: deepFormJSON(depth)}}, true)
		if err != nil || invalid || patch.TreeDepth != depth || patch.Tree == nil {
			t.Fatalf("depth %d: %+v invalid=%v err=%v", depth, patch, invalid, err)
		}
	}
}

// TestCourseEnrolmentFormJSONBands pins course-subscriptions/add (X15):
// multiple_quote D >= 9929 and events D >= 9943 are the plain 500 page with
// nothing written; the stock create keeps its unmeasured past-500 refusal.
func TestCourseEnrolmentFormJSONBands(t *testing.T) {
	for _, test := range []struct {
		name    string
		depth   int
		tooDeep bool
	}{
		{"multiple_quote", 600, false},
		{"multiple_quote", 9928, false},
		{"multiple_quote", 9929, true},
		{"events", 9942, false},
		{"events", 9943, true},
	} {
		if got := courseEnrolmentFormJSONTooDeep(map[string]int{test.name: test.depth}); got != test.tooDeep {
			t.Fatalf("%s depth %d: tooDeep=%v, want %v", test.name, test.depth, got, test.tooDeep)
		}
	}
	if courseEnrolmentFormJSONUnmeasured(map[string]int{"multiple_quote": 500}) {
		t.Fatal("500 levels are within the stock create's previous boundary")
	}
	if !courseEnrolmentFormJSONUnmeasured(map[string]int{"events": 501}) {
		t.Fatal("501 levels are past the stock create's previous boundary")
	}
	if !errors.Is(errCourseEnrolmentFormJSONTooDeep, refusal.ErrAppState) {
		t.Fatalf("%v is not an application-state error", errCourseEnrolmentFormJSONTooDeep)
	}
}

// TestCourseSubscriptionUpdateFormJSONBands pins
// course-subscriptions/<pk>/update (X15): multiple_quote D >= 9935 and events
// D >= 9942 are the plain 500 page with nothing written.
func TestCourseSubscriptionUpdateFormJSONBands(t *testing.T) {
	for _, test := range []struct {
		name    string
		depth   int
		tooDeep bool
	}{
		{"multiple_quote", 600, false},
		{"multiple_quote", 9934, false},
		{"multiple_quote", 9935, true},
		{"events", 9941, false},
		{"events", 9942, true},
	} {
		if got := courseSubscriptionFormJSONTooDeep(map[string]int{test.name: test.depth}); got != test.tooDeep {
			t.Fatalf("%s depth %d: tooDeep=%v, want %v", test.name, test.depth, got, test.tooDeep)
		}
	}
	if !errors.Is(errCourseSubscriptionFormJSONTooDeep, refusal.ErrAppState) {
		t.Fatalf("%v is not an application-state error", errCourseSubscriptionFormJSONTooDeep)
	}
}

// TestInvoicePartyFormJSONBands pins invoice-customers/add (X15): D = 9942 is
// today's shallow answer, D = 9943 the plain 500 page; the supplier update's
// boundary lives in the nested payment reader (9942).
func TestInvoicePartyFormJSONBands(t *testing.T) {
	if invoiceCustomerFormJSONTooDeep(9942) {
		t.Fatal("9942 must answer as for a shallow value")
	}
	if !invoiceCustomerFormJSONTooDeep(9943) || !invoiceCustomerFormJSONTooDeep(10000) {
		t.Fatal("9943 and deeper must answer the 500 page")
	}
	if invoiceSupplierNestedJSONDepth != 9942 {
		t.Fatalf("supplier nested JSON depth %d, want 9942", invoiceSupplierNestedJSONDepth)
	}
	for _, err := range []error{errInvoiceSupplierFormJSONTooDeep, errInvoiceCustomerFormJSONTooDeep} {
		if !errors.Is(err, refusal.ErrAppState) || errors.Is(err, refusal.ErrNotPorted) {
			t.Fatalf("%v is not an application-state error", err)
		}
	}
}
