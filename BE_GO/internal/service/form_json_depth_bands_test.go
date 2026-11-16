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

// TestCourseEnrolmentFormJSONBands pins course-subscriptions/add and the stock
// router create (X15): multiple_quote or events D >= 9943 is DRF's validation
// RecursionError (the plain 500 page, nothing written); multiple_quote
// 9929..9942, where Django's answer depends on the course and the other
// fields, is handed off before any write.
func TestCourseEnrolmentFormJSONBands(t *testing.T) {
	for _, test := range []struct {
		name  string
		depth int
		want  error
	}{
		{"multiple_quote", 0, nil},
		{"multiple_quote", 600, nil},
		{"multiple_quote", 9928, nil},
		{"multiple_quote", 9929, refusal.ErrNotPorted},
		{"multiple_quote", 9934, refusal.ErrNotPorted},
		{"multiple_quote", 9942, refusal.ErrNotPorted},
		{"multiple_quote", 9943, refusal.ErrAppState},
		{"multiple_quote", 10000, refusal.ErrAppState},
		{"events", 600, nil},
		{"events", 9942, nil},
		{"events", 9943, refusal.ErrAppState},
	} {
		err := courseEnrolmentFormJSONBand(map[string]int{test.name: test.depth})
		if test.want == nil && err != nil || test.want != nil && !errors.Is(err, test.want) {
			t.Fatalf("%s depth %d: %v, want %v", test.name, test.depth, err, test.want)
		}
	}
	if errors.Is(errCourseEnrolmentFormJSONBand, refusal.ErrAppState) || errors.Is(errCourseEnrolmentFormJSONTooDeep, refusal.ErrNotPorted) {
		t.Fatal("the dump band is a handoff and the validation band application state")
	}
}

// TestCourseEnrolmentStockFormJSONDepth pins the reader's depth on the measured
// bands: a form value past 500 levels parses and carries its depth, so the
// stock create answers the band instead of refusing the input.
func TestCourseEnrolmentStockFormJSONDepth(t *testing.T) {
	for _, depth := range []int{501, 600, 9900, 9929, 9934, 9980} {
		write, invalid := dto.ParseCourseSubscriptionWrite(
			domain.RequestObject{{Name: "multiple_quote", Value: deepFormJSON(depth)}}, true)
		if invalid != nil || write.FormJSONDepth["multiple_quote"] != depth || write.Quote == nil {
			t.Fatalf("depth %d: depth=%v quote=%v invalid=%v", depth, write.FormJSONDepth["multiple_quote"], write.Quote, invalid)
		}
		write, invalid = dto.ParseCourseSubscriptionWrite(
			domain.RequestObject{{Name: "events", Value: deepFormJSON(depth)}}, true)
		if invalid != nil || write.FormJSONDepth["events"] != depth || write.Events == nil {
			t.Fatalf("events depth %d: depth=%v events=%v invalid=%v", depth, write.FormJSONDepth["events"], write.Events, invalid)
		}
	}
}

// TestCourseEnrolmentDuplicateFormJSONKey pins the shape the parity cases use
// for a deep quote text: json.loads keeps the last of two equal keys, so the
// parsed quote is the shallow one at the text's own depth (the row, the
// payment meta and the audit reprs stay shallow).
func TestCourseEnrolmentDuplicateFormJSONKey(t *testing.T) {
	text := `{"quote_id": "q1", "amount":` + deepFormJSON(9900) + `, "amount": "25,50"}`
	depth := dto.AuditJSONDepth(text)
	write, invalid := dto.ParseCourseSubscriptionWrite(domain.RequestObject{{Name: "multiple_quote", Value: text}}, true)
	quote, isObject := write.Quote.(domain.RequestObject)
	if invalid != nil || !isObject || write.FormJSONDepth["multiple_quote"] != depth || depth < 9000 {
		t.Fatalf("depth=%v (text %v) invalid=%v quote=%T", write.FormJSONDepth["multiple_quote"], depth, invalid, write.Quote)
	}
	if amount, present := quote.Lookup("amount"); !present || amount != "25,50" || len(quote) != 2 {
		t.Fatalf("parsed quote = %+v, want the shallow last duplicate", quote)
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
